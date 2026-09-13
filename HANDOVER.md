# HANDOVER — Buy or Wait? agent

Context transfer from the claude.ai session that built this solution. Read this before changing
anything under `code/`. The full conversation transcript is in `log.txt` (gitignored, kept
locally for submission).

## Status

- `python3 code/main.py` writes `output.csv` at the repo root: 250 rows, deterministic
  (byte-identical on re-run), ~7 s, zero model calls.
- `python3 code/evaluation/main.py validate` → VALID (schema, bounds, plan/option match,
  flexible-only spending changes).
- `python3 code/evaluation/main.py samples` → status 25/25, method 25/25, plan 24/25,
  changes 24/25, earliest date 24/25, median |error| of `amount_safe_to_pay` 0.6 % (mean 2.8 %).
  Decisions differ on request_12 (changes), request_17 (earliest date) and request_19 (plan);
  request_05, 10, 14, 15 and 20 differ only in the amount.

## Architecture (code/buyorwait)

| Module | Responsibility |
|---|---|
| `data.py` | CSV loading, typed frames, dated FX conversion, `OUTPUT_COLUMNS` |
| `recurrence.py` | detects recurring series per (category, direction): monthly fixed-day chains and every-N-day chains; base-amount estimation |
| `forecast.py` | rebuilds the user's state for one request, applies evidence facts, projects flows, simulates the 90-day balance path, computes `amount_safe_to_pay` and `earliest_date_for_full_payment` |
| `evidence.py` | message template reader (EN/ID), OCR of receipt images with amount-in-words cross-check, optional Anthropic LLM/vision fallback |
| `planner.py` | candidate plans, ranking, spending-change search, explanation text, `verify()` |
| `pipeline.py` | orchestration, debug JSONL, usage accounting |

## Rules that were reverse-engineered from the 25 solved samples

Changing any of these will move the score; re-run `evaluation/main.py samples` after touching them.

1. **Recurrence is detected on `event_date`; cash moves on `settlement_date`.**
2. **Horizon:** monthly items are projected for the request month plus the next two calendar
   months; every-N-day items to R+92 (`PERIODIC_HORIZON_DAYS`). A periodic occurrence landing
   exactly on the request date is excluded; a monthly one is included.
3. **First projected gap** (`FIRST_GAP = {21: 15, 7: 6}` in `recurrence.py`): three-week chains
   reserve their next occurrence ~15 days out, then continue every 21 days (pure-21 and pure-15 both
   scored worse); weekly chains reserve theirs 6 days out, then every 7 days, which reproduces the
   reference's weekly counts on request_04, 06 and 15. Tried and rejected on the samples: weekly
   gaps of 5 or 4 and a one-day-early gap on every period (amount errors up to 41 % on requests
   08, 14, 19, 22, 24, 25); skipping occurrences 1–2 days after the request date (cannot add
   request_04's missing occurrence; the 2-day version scores one flag higher via request_12 but
   breaks the request_20 and 24 amounts); re-anchoring at the request date (median error 9–11 %);
   settlement-date cadence and modal-weekday phase (no effect).
4. **Within-day stage order** (`STAGE` in `forecast.py`): existing pending/scheduled rows →
   every-N-day spending → income → monthly commitments → the request's own payment. The balance
   is checked after each stage.
5. **Base amounts:** constant series use the last value; a level change uses the last two equal
   values; reducible items use `minimum_allowed_amount / ratio` (0.5, or 0.4 for shopping);
   otherwise the centre of the feasible interval given the observed noise band
   (`NOISE_TIGHT = 0.12`, `NOISE_WIDE = 0.28`). Outliers stay in the schedule but not in the
   estimate (`regular_mask`).
6. **Stale series** (monthly gap > 45 d, periodic > 2p + 2) are treated as stopped; a "final"
   payroll stops income; a scheduled next-salary row sets both the level and the date.
7. **Plan feasibility tolerance** (`PLAN_TOL_FRAC = 0.012` in `forecast.py`) applies *only* when
   judging whether a candidate plan stays above the minimum — never to `amount_safe_to_pay` or
   `earliest_date_for_full_payment`.
8. **Message semantics** (`evidence.py::TEMPLATES` + `read_message`): a salary raise replaces the
   history level from its date; "confirmed base salary, commission pending" keeps the *history*
   base and drops the commission; gig payout pending excludes all variable earnings; a confirmed
   invoice replaces the freelance series with a single credit; arrears are already settled and are
   not added; a moved payday overrides the next date only; scam prize notices yield no facts.
9. **Image amounts**: keyword totals (Net Pay / Balance Due / Total paid / Amount payable …)
   cross-checked against the amount-in-words line. Item-only subtotals (Item Bill, Subtotal,
   Total items) are never read; when nothing else is readable the amount stays unknown, and the
   settled event keeps its slot in its recurring chain but is left out of the base estimate.
   `image_14` is handwritten; its value lives in
   `code/cache/verified_image_amounts.json` and is replaced by a real vision call when
   `ANTHROPIC_API_KEY` is set.

## Known open items / ideas

- request_12: the reference answer uses no spending change where the forecast needs one
  (`reduce_to:event_1017`). request_06, the mirror case, was a weekly-chain timing difference and
  is fixed by `FIRST_GAP[7] = 6`. Skipping every-N-day occurrences due within 2 days after the
  request date also flips request_12 to match, but breaks the request_20 and 24 amounts (67 % and
  17 % error).
- request_19 (30-minute investigation on 2026-09-13, no general rule found): the reference's
  28,820 vs our 29,732.13 is a reservation difference, not rounding. Headroom is 199,545 − 92,800
  = 106,745 and the binding checkpoint is 2024-09-14, the day before payday. We reserve 77,012.87
  up to then (rent 36,100, loan 11,850, cloud 395, noise-estimated utilities 5,881.31, groceries
  4,462.43, transport 3,213.63, clinic 9,030.50, shopping anchor 6,080); the reference implies
  77,925 (+912.13).
  * (a) Extra every-N-day occurrence: ruled out. Groceries (7 d) and transport (14 d) each project
    exactly one occurrence before payday, with or without `FIRST_GAP`; one more would cost
    4,462.43 / 3,213.63, far more than the gap.
  * (b) Pending/scheduled row on the wrong date: ruled out — user_19 has no such rows.
  * (c) Variable bases ~3 % low: a uniform ×1.0318 on noise-estimated debit bases reproduces
    28,820, but as a rule it regresses the other samples (×1.03: changes 23→20, earliest 24→22,
    median error 0.81 %→1.99 %; ×1.015: median 1.30 %).
  * Partial cause, 312.15 of the 912.13 — fixed: `image_04` shows only the item subtotal of
    event_1700 ("Item Bill 2854.00"; the order total is cropped out), which the OCR reader used to
    report as the amount, pulling the grocery base down to the mid-range 4,462.43. Subtotals are no
    longer read: the amount stays unknown, the event keeps its chain slot, the base is 4,774.57 and
    the safe amount is now 29,419.98 (error 2.08 %). Tuning `OUTLIER_LOW` to 0.65 or an
    infeasible-interval fallback would also have hidden the value, but only this sample supported them.
  * Still unexplained: ~600. With groceries at 4,774.57 the four noise-estimated items sum to
    22,900.02, while the reference implies 23,500 for them (same item set, shopping at 6,080).
- The LLM layer is wired but unexercised; enabling it (`ANTHROPIC_API_KEY`) only affects messages
  the templates cannot classify (currently none) and unreadable images (currently one).
- `code/evaluation/decisions_debug.jsonl` holds per-request series, facts, candidates and notes —
  the fastest way to diagnose a wrong decision.

## Experiments

Scored with `evaluation/main.py samples` and `evaluation/compare.py` against the previous output, and
kept here so they are not repeated.

**First projected gap, one period at a time** (2026-09-13), each on top of
`FIRST_GAP = {21: 15, 7: 6}` (baseline: status 25, method 25, plan 24, changes 24, earliest 24,
median error 0.61 %, mean 2.80 %, 19 amounts within 2 %). None adopted: no total improves and two
break a sample amount that was right.

| variant | status | method | plan | changes | earliest | median | mean | within 2 % | blast radius on the 250 |
|---|---|---|---|---|---|---|---|---|---|
| `14: 13` | 25 | 25 | 24 | 24 | 24 | 0.61 % | 2.80 % | 19 | 1 row (request_212 amount −36 %) |
| `10: 9` | 25 | 25 | 24 | 24 | 24 | 0.61 % | 3.52 % | 18 | 11 rows, 3 statuses; request_24 error 0.9 % → 19 % |
| `5: 4` | 25 | 25 | 24 | 24 | 24 | 0.61 % | 4.41 % | 18 | 13 rows, no status; request_25 error 1.0 % → 41 % |

## Conventions to keep

- No hardcoded labels, no organizer-only files, secrets from environment variables only.
- Every output row passes `planner.verify()` before it is written; a failure falls back to the
  conservative `not_recommended` row and is recorded in `evaluation/run_summary.json`.
- `log.txt` is append-only and may be committed since upstream AGENTS.md dropped the never-commit rule (keep the repo private).
