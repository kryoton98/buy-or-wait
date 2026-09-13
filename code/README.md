# Buy or Wait? — Financial Decision Agent

A terminal-based agent that decides, for every purchase or payment request, whether the user should **pay in full**,
**pay partially**, **use an installment option**, **wait**, or **not proceed**. It rebuilds each user's cash position
from their financial history, reads messages and receipt images as untrusted evidence, simulates the balance day by
day for 90 days, and only recommends plans that keep the balance above the user's `minimum_balance_to_keep`.

Built for the [HackerRank Orchestrate September 2026](https://github.com/interviewstreet/hackerrank-orchestrate-september26)
challenge, *Buy or Wait?*.

---

## Architecture

```
dataset/   250 requests · 25,342 financial events · 215 messages · 16 receipt images
        │
        ▼
┌───────────────────────────┐
│ 1. Load                   │  Typed columns, dated FX conversion, profile lists
│    (data.py)              │
└─────────────┬─────────────┘
              ▼
┌───────────────────────────┐
│ 2. Evidence reader        │  30 bilingual (EN / ID) message templates → structured facts
│    (evidence.py)          │  OCR totals for blank amounts, checked against the amount in words;
│                           │  item-only subtotals rejected
└─────────────┬─────────────┘
              │ facts: salary_amount, income_stopped, unconfirmed_income, one_time_credit …
              ▼
┌───────────────────────────┐
│ 3. State reconstruction   │  Recurring series from settled history (monthly and every N days),
│    (recurrence.py,        │  mid-range base amounts, stale series stopped, pending and scheduled
│     forecast.py)          │  debits reserved, facts applied
└─────────────┬─────────────┘
              │ dated cash flows for the next 90 days
              ▼
┌───────────────────────────┐
│ 4. Balance simulation     │  Staged within-day balance path → amount_safe_to_pay and
│    (forecast.py)          │  earliest_date_for_full_payment
└─────────────┬─────────────┘
              ▼
┌───────────────────────────┐
│ 5. Planner                │  Full payment (with up to 3 permitted spending changes), installments,
│    (planner.py)           │  partial schedule, wait: ranked in the problem statement's order
└─────────────┬─────────────┘
              ▼
┌───────────────────────────┐
│ 6. Verification           │  Contract checks and a re-simulation of the chosen plan;
│    (planner.verify)       │  any failure falls back to a conservative row
└─────────────┬─────────────┘
              ▼
          output.csv   (+ evaluation/run_summary.json, evaluation/decisions_debug.jsonl)

Optional model layer (Anthropic or Google Gemini, only when an API key is set):
  • fallback reader for messages the templates cannot place and images OCR cannot read
  • --llm-audit: after output.csv is written, reads every message with the model and compares
    it with the rules → evaluation/llm_audit.md (never changes a decision)
```

### Why this design?

The 25 solved samples encode a precise decision style: which items are reserved before payday, how variable spending
is estimated, when a spending change beats waiting. That style can be reproduced with explicit rules, so the rules were
reverse-engineered from the samples and kept only when they held across all of them. The result is **deterministic**
(a second run writes a byte-identical `output.csv`), **explainable** (every row's series, facts and candidate plans are
in `decisions_debug.jsonl`), **verified** (every row is re-simulated before it is written) and **free**: the submitted
output needs zero model calls.

A language model is still useful, as a reader and an auditor rather than a decision-maker. Messages and images are
untrusted evidence: the templates extract only financial facts and never follow embedded instructions. The model is
wired as a fallback for evidence the rules cannot read, and a full audit with Gemini showed why it does not drive the
decisions: in 32 of its 94 disagreements with the rules it extracted facts the rules deliberately ignore (unapproved
invoices, the base salary behind a pending commission, arrears), and applying them would break the match on sample 11.

---

## Project structure

```
.
├── code/
│   ├── main.py                          # CLI: decides every request, writes output.csv
│   ├── buyorwait/
│   │   ├── data.py                      # CSV loading, typed columns, dated FX conversion
│   │   ├── evidence.py                  # Message templates, OCR reader, optional LLM client
│   │   ├── recurrence.py                # Series detection, base amounts, projection dates
│   │   ├── forecast.py                  # State reconstruction, facts, 90-day simulation
│   │   ├── planner.py                   # Candidate plans, ranking, explanations, verify()
│   │   ├── pipeline.py                  # Orchestration, debug output, usage counters
│   │   └── llm_audit.py                 # Report-only model-vs-rules audit of the messages
│   ├── prompts/                         # System prompts for message and image extraction
│   ├── cache/
│   │   ├── ocr_cache.json               # Cached tesseract output (runs without tesseract)
│   │   ├── verified_image_amounts.json  # Hand-verified value for the handwritten bill
│   │   └── llm_cache.json               # Model responses (generated, not committed)
│   ├── evaluation/
│   │   ├── main.py                      # validate (contract checks) and samples (scorer)
│   │   ├── compare.py                   # Blast radius: diff of two output.csv files
│   │   ├── experiments/estimators.py    # Base-amount estimator comparison on the samples
│   │   ├── run_summary.json             # Counters of the last run (generated)
│   │   ├── decisions_debug.jsonl        # Per-request series, facts, candidates (generated)
│   │   ├── llm_audit.md                 # Gemini audit of the 215 message readings, triaged
│   │   └── usage_report.md              # Model calls, tokens and cost of the final run
│   ├── requirements.txt
│   └── README.md                        # This file
├── dataset/                             # Challenge data: profiles, events, FX rates, requests,
│                                        #   payment options, messages, images, output template
├── output.csv                           # Predictions for the 250 requests (generated)
├── HANDOVER.md                          # Rules, experiments, open items, changes since submission-v1
├── AGENTS.md, CLAUDE.md                 # Instructions for AI coding agents
├── problem_statement.md                 # Challenge specification
├── log.txt                              # Chat transcript of the AI-assisted development
└── code.zip                             # Submission archive of code/ (generated, not committed)
```

---

## Quickstart

### 1. Clone and install

```bash
git clone https://github.com/kryoton98/buy-or-wait.git
cd buy-or-wait
pip install -r code/requirements.txt       # pandas>=2.0, numpy>=1.24
```

Python 3.10 or newer (developed on 3.12). `tesseract-ocr` is optional: the OCR text of all 16 images is cached in
`code/cache/ocr_cache.json`. Run every command from the repository root.

### 2. Decide every request

```bash
python3 code/main.py --llm off
```

Reads `dataset/`, writes `output.csv` at the repository root in about 5 seconds and records its counters in
`code/evaluation/run_summary.json`. Repeating the run produces a byte-identical file. `--llm off` guarantees that no
model is consulted even when an API key is set; without a key, plain `python3 code/main.py` gives the same output.

### 3. Validate and score

```bash
python3 code/evaluation/main.py validate    # schema, bounds, plan/option match, flexible-only changes
python3 code/evaluation/main.py samples     # re-decides the 25 solved samples and scores them
```

### 4. Measure the blast radius of a change

```bash
cp output.csv /tmp/before.csv
# ... change the code, then repeat step 2 ...
python3 code/evaluation/compare.py /tmp/before.csv output.csv
```

Prints how many of the 250 rows differ per column (formatting-only differences counted separately), how far
`amount_safe_to_pay` moved, status and method transition matrices, and every changed `request_id`.

### 5. Optional: audit the message reader with a model

```bash
export GEMINI_API_KEY=...                  # leave ANTHROPIC_API_KEY unset to select Gemini
python3 code/main.py --llm off --llm-audit
```

Decides first, then reads all 215 messages with `gemini-3.5-flash-lite` and writes `code/evaluation/llm_audit.md`
(about 15 minutes at the free-tier request spacing). Decisions and `output.csv` are unaffected.

### Other options

```bash
python3 code/main.py --requests samples --output /tmp/samples.csv   # decide only the 25 solved samples
python3 code/main.py --dataset path/to/dataset --output path/to/output.csv
python3 code/evaluation/experiments/estimators.py --details         # estimator comparison
```

---

## Output schema

Each row of `output.csv` contains:

| Column | Type | Description |
|---|---|---|
| `request_id` | string | One row per request in `dataset/requests.csv` |
| `amount_safe_to_pay` | number | Largest payment on the request date that keeps every projected balance at or above the minimum, before spending changes; between 0 and the requested amount |
| `affordability_status` | enum | See below |
| `recommended_payment_method` | enum | See below |
| `payment_plan` | string | Chronological `YYYY-MM-DD:amount` entries joined by a vertical bar, or `none` |
| `earliest_date_for_full_payment` | date | First day a single full payment is safe; the request date when affordable now; empty when no date within the forecast works |
| `spending_changes_needed` | string | `none`, or up to three `stop:<event_id>` / `reduce_to:<event_id>:<new_amount>` actions on flexible expenses in categories the user allows |
| `decision_explanation` | string | Short explanation grounded in the forecast |

### Affordability status values

| Value | Meaning |
|---|---|
| `affordable_now` | One full payment today is safe, with no spending changes |
| `affordable_with_plan` | The full request is completed through permitted spending changes, a supplied installment option or a two-payment partial schedule |
| `affordable_later` | One full payment becomes safe on a later date |
| `not_affordable` | No acceptable plan keeps the minimum balance within the forecast |

### Payment method values

| Value | Plan |
|---|---|
| `full_payment` | Pay the full amount today, possibly after spending changes |
| `partial_payment` | `amount_safe_to_pay` today and the remainder on the earliest safe date, by the desired completion date |
| `installments` | A supplied payment option with no more payments than the user's `max_installment_months` |
| `wait` | One full payment on the earliest safe date |
| `not_recommended` | Do not proceed; the plan is `none` |

---

## Key components

### Evidence reader (`evidence.py`)

Every message is classified into one of 30 English or Indonesian templates and turned into facts. Embedded
instructions are never executed: a prize notice asking for a release fee yields no facts.

| Message situation | Facts produced |
|---|---|
| Salary raised, first salary, salary resumes, foreign-currency salary | `salary_amount` (permanent, from the stated date) |
| Regular salary with a one-time arrears adjustment | `salary_amount` for the regular pay only; the arrears are already settled and not added |
| Temporary pay, salary reduced by unpaid leave | `salary_amount` for the next payroll only |
| Payday moved | `salary_date` |
| Seasonal contract or employment ended | `income_stopped` |
| Bonus pending, commission pending, gig payout pending, second household income ended | `unconfirmed_income` with scope `bonus`, `commission`, `all_variable` or `secondary` |
| Client approved one invoice | `one_time_credit` for that invoice, and the other freelance income stopped |
| Lease renewal raises rent | `rent_change` (multiplier) |
| Transfer between the user's own accounts | `internal_transfer` |
| Receipt mentioning a confirmed salary credit | `salary_amount` in the stated currency |
| Pending refund, portfolio value, prizes, reimbursement, settled sale, failed debit retry, disputed charge, card minimums, foreign bill, regular salary confirmed | No facts: pending or unrealised money is not income, and scheduled rows stay reserved |

**Receipt images.** A blank event amount is read from `dataset/media/images/<image_id>.png` with three tesseract
passes. The first keyword total in priority order wins (Net Pay, Total paid, Balance Due, Amount due till, Grand Total,
Total Amount Received, Amount Payable, Total Bill Amount, Total, Cash Paid, Amount Due, Balance), and the amount
written in words overrides the digits when they disagree. Item-only subtotals (Item Bill, Item Total, Subtotal, Total
items) are never taken as the amount. 14 of the 16 images are read this way; the handwritten pharmacy bill uses a
hand-verified value, and `image_04` shows only an item subtotal, so its amount stays unknown. A settled event with an
unknown amount keeps its slot in its recurring chain but stays out of the base estimate.

### Recurrence and forecast rules (`recurrence.py`, `forecast.py`)

| Rule | Detail |
|---|---|
| History | Only `settled` rows before the request date; recurrence is detected on `event_date`, cash moves on `settlement_date` |
| Series | Per category and direction: fixed day-of-month chains (±2 days) or every-N-day chains (N from 5 to 25, ±1 day) |
| Base amount | Constant series keep their value, a level change its new level, reducible items `minimum_allowed_amount / 0.5` (0.4 for shopping); other varying series the mid-range of their regular amounts (outliers outside 0.6–1.5 × the median excluded) |
| Stale series | Monthly gap over 45 days, every-N-day gap over 2N + 2 days, a "final" payroll or an ended contract stops the series |
| Horizon | Monthly items for the request month and the next two calendar months; every-N-day items to the request date + 92 days, not on the request date itself |
| First projected step | `FIRST_GAP = {21: 15, 7: 6}`: three-week chains reserve their next item 15 days out and weekly chains 6 days out, then keep their cadence |
| Pending and scheduled rows | Debits reserved on their settlement date; pending credits, bonuses and refunds never counted; a scheduled next salary sets the level and the date |
| Currency | Foreign-currency cash events converted with the `exchange_rates.csv` rate for their settlement date, in the stated direction |
| Within-day order | Pending and scheduled rows → every-N-day spending → income → monthly commitments → the request's own payment; the balance is checked after each stage |

### Planner and verification (`planner.py`)

- `amount_safe_to_pay` is the lowest projected balance minus the minimum, capped at the request;
  `earliest_date_for_full_payment` is the first day a single full payment keeps every later checkpoint above the minimum.
- Candidates: full payment today, with the lightest set of up to three `stop` / `reduce_to` changes on flexible
  expenses the user allows (never protected categories); every supplied installment option within
  `max_installment_months`; a two-payment partial schedule when the request and the user allow it; waiting for the
  earliest date.
- Ranking follows the statement: completes by the desired date › no spending changes › lowest total paid › earlier
  start › fewer payments › lowest option id.
- A small allowance (`PLAN_TOL_FRAC`, 0.3 % of the reserved outflow) applies only when judging whether a plan stays
  above the minimum, never to the two numeric outputs.
- `verify()` checks bounds, `affordable_now` ⇒ earliest date = request date, partial plans of exactly two payments
  that add up, installment schedules identical to a supplied option, at most three changes on flexible events, and
  re-simulates the chosen plan. A failure is replaced by a conservative `not_recommended` row.

### Optional model layer (`evidence.py::LLMExtractor`, `llm_audit.py`)

- **Provider:** Anthropic Messages API (`claude-sonnet-4-5`) when `ANTHROPIC_API_KEY` is set, otherwise Google Gemini
  (`gemini-3.5-flash-lite`) when `GEMINI_API_KEY` is set; `BUYORWAIT_MODEL` overrides the model.
- **Gemini client:** REST `generateContent` through `urllib` (no extra dependency), key in the `x-goog-api-key` header,
  system prompt from `code/prompts/`, temperature 0, JSON output, minimal thinking level (dropped if a model rejects
  it); token counts come from `usageMetadata`, with thinking tokens counted as output.
- **Rate limits:** requests spaced 4 s apart; 429 and 503 responses retried up to 6 times after the server's
  `RetryInfo` or `Retry-After` delay, or 2, 4, 8 … s of backoff (at most 60 s); a longer server delay, such as an
  exhausted daily quota, is not waited out.
- **Cache and validation:** responses cached in `code/cache/llm_cache.json`; model output is used only when it is JSON
  with a known template kind, and only known fact types with parsable dates and amounts are kept.
- **Use in decisions:** only with `--llm auto`, and only for messages the templates cannot place (none in this dataset)
  and images OCR cannot read.
- **Audit:** `--llm-audit` runs after `output.csv` is written and compares each message's kind and facts with the
  rule-based reading; it never touches the readings the decisions use.

---

## Evaluation results (25 solved samples)

```
$ python3 code/evaluation/main.py samples
...
sample score:
  status_ok          25/25
  method_ok          25/25
  plan_ok            24/25
  changes_ok         24/25
  earliest_ok        24/25
  safe_within_2pct   18/25
  mean |rel err| of amount_safe_to_pay: 2.24%   median: 0.50%
```

`python3 code/evaluation/main.py validate` reports `output.csv: VALID`. Decisions still differ from the reference on
request_12 (change set), request_17 (earliest date) and request_19 (partial-payment split); requests 05, 10, 13, 14, 15
and 25 differ only in `amount_safe_to_pay`.

How the score moved with each kept change, measured with the same scorer:

| Version | Status | Method | Plan | Changes | Earliest | Within 2 % | Median error | Mean error |
|---|---|---|---|---|---|---|---|---|
| `submission-v1` | 24 | 24 | 23 | 23 | 24 | 17 | 0.81 % | 4.64 % |
| + item subtotals never read from receipts | 24 | 24 | 23 | 23 | 24 | 17 | 0.81 % | 4.59 % |
| + weekly spending reserved one day early (`FIRST_GAP[7] = 6`) | 25 | 25 | 24 | 24 | 24 | 19 | 0.61 % | 2.80 % |
| + mid-range base estimate | 25 | 25 | 24 | 24 | 24 | 18 | 0.50 % | 2.24 % |
| + plan tolerance 1.2 % → 0.3 % | 25 | 25 | 24 | 24 | 24 | 18 | 0.50 % | 2.24 % |

---

## LLM audit and token usage (final run)

The final run, `python3 code/main.py --llm off --llm-audit`, made its decisions without any model call and then audited
the message reader with Gemini:

| Item | Value |
|---|---|
| Provider and model | Google `gemini-3.5-flash-lite` (REST `generateContent`) |
| Model calls | 215, one per message, no retries |
| Tokens | 117,388 input + 20,583 output = 137,971 (551.9 per request over 250 requests) |
| Cost | US$ 0.00 on the free tier; US$ 0.0867 at paid-tier list prices (US$ 0.30 / 1M input, US$ 2.50 / 1M output) |
| Agreement with the rules | 121 agree, 94 disagree, 0 without a usable reading |
| Triage of the 94 disagreements | 55 different label or redundant fact, no forecast effect · 32 facts the rules deliberately ignore · 7 would change a forecast input |

The seven forecast-changing cases are "household income ended" messages that state a remaining monthly salary. The
rules keep the history salary, which is exactly 62 % of the stated amount, the same structure as pending-commission
messages (exactly 60 %), where sample 11 shows the reference follows the history component. With no solved sample
covering this template, the history reading is kept; the alternative would have moved request_42, request_50 and
request_58. Details: `code/evaluation/llm_audit.md` and `code/evaluation/usage_report.md`.

---

## Requirements

```
pandas>=2.0
numpy>=1.24
```

Install with `pip install -r code/requirements.txt`. Optional: the `tesseract-ocr` binary (only needed if an image is
not in the OCR cache) and an API key for the model layer. Python 3.10 or newer.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | No | Enables the model layer with the Anthropic Messages API |
| `GEMINI_API_KEY` | No | Enables the model layer with Google Gemini when `ANTHROPIC_API_KEY` is not set |
| `BUYORWAIT_MODEL` | No | Overrides the model of the selected provider |
| `BUYORWAIT_LLM_MIN_INTERVAL` | No | Seconds between model requests (default 4 for Gemini, 0 for Anthropic) |

Keys are read from the environment only: no `.env` file is loaded and nothing is hard-coded. Without a key the model
layer stays off and the whole pipeline runs offline.

---

## Chat transcript logging

`log.txt` at the repository root is the transcript of the AI-assisted development: every session start and every
conversation turn with the coding assistants (claude.ai for the first version, Claude Code for the tuning, the Gemini
provider and the audit) is appended with the prompt, a summary, the actions taken and the tool name, following
`AGENTS.md`. The agent itself does not write to it, and no secrets are logged.

---

## Design decisions & trade-offs

**Why rules for decisions, and a model only as reader and auditor?**
The solved samples reward one precise decision style, and explicit rules reproduce it exactly, run in seconds and
give the same answer every time. The Gemini audit confirmed the split: most disagreements were labels, and a third
were facts the rules ignore on purpose; letting the model decide would have reintroduced unapproved income.

**Why the mid-range for noisy spending?**
Amounts in this data vary uniformly around a base, and for uniform noise the mid-range is the unbiased,
minimum-variance estimate whatever the band width. The previous noise-interval centre carried an upward bias of about
2w² / ((n + 1)(1 − w²)) and three tuned constants; switching lowered the median error from 0.61 % to 0.50 %.

**Why reserve weekly spending one day early?**
Samples 04, 06 and 15 each differed from the reference by exactly one weekly item before payday. A 6-day first step
reproduces all three; skipping items just after the request date, re-anchoring chains at the request date or other gaps
either missed request_04 or broke correct samples.

**Why a 0.3 % plan tolerance?**
The former 1.2 % allowance had been calibrated on top of the estimator's implicit margin. After the mid-range change, a
sweep from 0 to 1.5 % gave the highest exact-match total at 0.3 %, the smallest tolerance with that total.

**Why never read an item subtotal from a receipt?**
An item subtotal leaves out delivery fees, taxes and discounts. Reading one understated a grocery order and dragged a
recurring estimate down; the amount now stays unknown unless a real total is readable.

**Why check the blast radius of every change?**
Twenty-five samples are few. Every change was scored on the samples and compared with `compare.py` across the 250
requests, reverted on any regression, and each rejected experiment is recorded in `HANDOVER.md` so it is not repeated.

**Known limitations**
- The rules were reverse-engineered from 25 solved samples, and the templates match this dataset's English and
  Indonesian phrasings; a new phrasing becomes `unknown` and only the optional model fallback can read it.
- request_12's change set trades off against request_21's through the plan tolerance, and no single value matches both.
  request_17's earliest date (2026-04-15 vs 2026-03-15) and request_19's partial split (the reference reserves about
  767 more before payday than the history supports) remain unexplained.
- `household_income_ended` has no solved sample; its history reading rests on the analogy with pending commissions.
- `image_04` shows only an item subtotal, so its amount stays unknown unless the vision fallback runs
  (`--llm auto` with a key).
- In the fallback path, an undated one-time credit returned by a model would stop `forecast.apply_facts`; a validation
  guard is proposed in `llm_audit.md` but not applied, and no decision used a model reading in the final run.
- Gemini's free-tier limits are set per project and not published, so requests are spaced 4 s apart and a full audit
  takes about 15 minutes.
- `run_summary.json` records wall-clock time, so it changes between runs even though `output.csv` does not.
