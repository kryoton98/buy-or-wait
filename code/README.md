# Buy or Wait? — financial decision agent

For every request in `dataset/requests.csv` the agent reconstructs the user's cash position
from `financial_profiles.csv` and `financial_events.csv`, reads the supporting messages and
images as *untrusted evidence*, forecasts the balance day by day for 90 days, and recommends
a payment plan that never takes the balance below `minimum_balance_to_keep`. It writes the
required `output.csv` (exact columns, one row per request) and verifies every row before
writing it.

## Run

```bash
pip install -r code/requirements.txt          # pandas, numpy
python3 code/main.py                          # writes <repo root>/output.csv (~5 s)
python3 code/evaluation/main.py validate      # contract checks on output.csv
python3 code/evaluation/main.py samples       # decide + score the 25 solved samples
python3 code/evaluation/compare.py BEFORE.csv AFTER.csv   # what changed between two outputs
python3 code/main.py --llm-audit              # also read every message with the model (needs an API key)
```

Options: `--dataset <dir>`, `--output <file>`, `--requests samples`, `--llm off`, `--llm-audit`, `--quiet`.
Secrets are read from the environment only (`ANTHROPIC_API_KEY` or `GEMINI_API_KEY`); nothing is hard-coded, no
organizer-only file is used, and the run is deterministic (a second run produces a byte-identical
`output.csv`).

## How it works

```
messages.csv ─┐  rule-based bilingual reader (EN / ID) ──┐
images.csv ───┤  OCR (tesseract, cached) + amount-in-words├─► structured facts ──┐
              └  optional LLM / vision fallback (Anthropic)┘                     │
financial_events.csv ─► recurrence detection ─► series (monthly / every-N-days) ─┼─► 90-day balance path
financial_profiles.csv ─► balance, minimum, protected / reducible / stoppable, methods, max months
request_payment_options.csv ─► installment schedules                              │
                                     candidate plans ─► ranking ─► verification ─► output.csv
```

1. **State reconstruction** (`buyorwait/recurrence.py`, `forecast.py`)
   * Only `settled` rows count as history; `pending`/`scheduled` debits are reserved on their
     settlement date; `failed`, `cancelled` and `unrealized` rows never touch cash; a scheduled
     "next confirmed salary" replaces the projected payroll on its date. Foreign-currency cash
     events are converted with the `exchange_rates.csv` row for their settlement date
     (`from_currency -> to_currency`).
   * Recurrence is detected per category and direction: fixed day-of-month items (rent,
     utilities, subscriptions, loans, payroll) and every-N-day spending (groceries, transport,
     dining). Same-date duplicates, outliers (bulk purchases, an unpaid-leave payslip) and settled
     events whose amount is unknown keep their slot in the schedule but do not affect the amount
     estimate. Series with no occurrence in the last cycle, "final payroll" rows and ended
     contracts are treated as stopped.
   * Base amounts: a constant series keeps its value and a level change its new level; reducible
     items use `minimum_allowed_amount / ratio` as an anchor; any other varying series uses the
     mid-range of its regular amounts, the unbiased estimate of the centre of the uniform noise
     these series show, whatever the width of the band.
   * Forecast: monthly items for the request month and the two following calendar months,
     every-N-day items to day 92. The first projected step is shortened for three-week and weekly
     chains (`FIRST_GAP = {21: 15, 7: 6}`), after which each chain keeps its cadence; an
     every-N-day item due on the request date itself is not reserved. Within a day: existing
     pending/scheduled rows → periodic spending → income → monthly items → the request payment;
     the balance is checked after each stage.
2. **Evidence** (`buyorwait/evidence.py`) – every message is classified into one of 30
   templates (salary raise/cut/date change, first salary, contract ended, commission or bonus
   pending, gig payout pending, confirmed invoice, rent increase, refunds, disputes, scam
   prizes, …) and turned into facts such as `salary_amount`, `salary_date`, `income_stopped`,
   `unconfirmed_income`, `one_time_credit`, `rent_change`. Embedded instructions are never
   executed. Blank amounts are read from the linked image with OCR (keyword totals such as
   *Net Pay*, *Balance Due*, *Total paid*, cross-checked against the amount-in-words line).
   Item-only subtotals (*Item Bill*, *Item Total*, *Subtotal*, *Total items*) are never taken as
   the amount: when nothing else is readable the amount stays unknown. OCR text is cached in
   `cache/ocr_cache.json` so the run also works without tesseract. One handwritten bill is
   shipped as a verified value in `cache/verified_image_amounts.json`. When an API key is set,
   `LLMExtractor` sends unreadable messages and images (on this dataset the subtotal-only receipt
   and the handwritten bill) to the model with the JSON-schema prompts in `prompts/` (temperature 0,
   JSON output, cached in `cache/llm_cache.json`, token-counted); results are validated before use.
   `ANTHROPIC_API_KEY` selects the Anthropic Messages API; otherwise `GEMINI_API_KEY` selects Google
   Gemini (`gemini-3.5-flash-lite` by default, `BUYORWAIT_MODEL` overrides) through the REST
   `generateContent` endpoint, with requests spaced 4 s apart (`BUYORWAIT_LLM_MIN_INTERVAL`) and
   429/503 responses retried with backoff. `--llm-audit` reads every message with the model after the
   decisions are written and reports its agreement with the rules in `evaluation/llm_audit.md`; it
   never changes a decision.
3. **Planning** (`buyorwait/planner.py`) – `amount_safe_to_pay` = largest payment today that
   keeps every future checkpoint ≥ minimum (no spending changes); `earliest_date_for_full_payment`
   = first day a single full payment is safe. Candidates: full payment today (with up to three
   permitted `stop:` / `reduce_to:` changes on flexible recurring expenses in categories the user
   is willing to adjust, never protected ones), every supplied installment option with
   `number_of_payments ≤ max_installment_months`, a two-payment partial schedule
   (`amount_safe_to_pay` today + remainder on the earliest date, only when the request and the
   user allow it), and waiting for the earliest date (only if the user accepts full payment).
   Ranking follows the statement: completes by `desired_completion_date` › no spending changes ›
   lowest total paid › earlier start › fewer payments › lowest `payment_option_id`. Change sets are
   the lightest sufficient ones (least monthly spending removed, stops before reductions).
4. **Verification** – bounds on `amount_safe_to_pay`, `affordable_now ⇒ earliest = request_date`,
   partial = exactly two payments summing to the request, installment schedule byte-equal to the
   option, ≤ 3 changes on flexible events only, and a re-simulation of the chosen plan. A small
   forecast-uncertainty allowance (`PLAN_TOL_FRAC`, 0.3 % of the reserved outflow) is applied only
   when judging plan feasibility; the two numeric outputs never use it.

## Evaluation

`python3 code/evaluation/main.py samples` re-decides the 25 solved samples with exactly the
production pipeline: 25/25 statuses, 25/25 methods, 24/25 plans, 24/25 change sets and 24/25
earliest dates; median absolute error of `amount_safe_to_pay` 0.50 %, mean 2.24 %. The remaining
decision differences are request_12 (change set), request_17 (earliest date) and request_19
(partial-payment split); `HANDOVER.md` records what was tried for each.

`python3 code/evaluation/compare.py BEFORE.csv AFTER.csv` compares two outputs: rows that differ
per column (formatting-only differences counted separately), how far `amount_safe_to_pay` moved,
status and method transition matrices, and every changed request_id. It was used to judge the
blast radius of each change. `code/evaluation/experiments/` holds reproducible experiments;
`estimators.py` scores alternative base-amount estimators on the samples.

`evaluation/run_summary.json` and `evaluation/decisions_debug.jsonl` are produced by every run
and contain, per request, the detected series, applied facts, candidate plans and notes.
Token usage of the final run is in `evaluation/usage_report.md`.

## Layout

```
code/
├── main.py                     CLI entry point
├── buyorwait/
│   ├── data.py                 CSV loading, FX conversion
│   ├── recurrence.py           series detection, base amounts, projection
│   ├── forecast.py             state reconstruction, evidence application, simulation
│   ├── evidence.py             message templates, OCR, optional LLM/vision layer
│   ├── planner.py              candidates, ranking, explanations, verification
│   ├── pipeline.py             orchestration and debug output
│   └── llm_audit.py            model-vs-rules audit of the message readings (report only)
├── prompts/                    LLM prompts (message / image extraction)
├── cache/                      OCR cache, verified image value, LLM cache (when used)
├── evaluation/
│   ├── main.py                 validator + sample scorer
│   ├── compare.py              diff of two output.csv files
│   ├── experiments/
│   │   └── estimators.py       base-amount estimator comparison on the samples
│   ├── run_summary.json        counters of the last run
│   ├── decisions_debug.jsonl   per-request series, facts, candidates and notes
│   └── usage_report.md         model calls, tokens and cost of the final run
└── requirements.txt
```
