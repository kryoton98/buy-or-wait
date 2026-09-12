# HANDOVER — Buy or Wait? agent

Context transfer from the claude.ai session that built this solution. Read this before changing
anything under `code/`. The full conversation transcript is in `log.txt` (gitignored, kept
locally for submission).

## Status

- `python3 code/main.py` writes `output.csv` at the repo root: 250 rows, deterministic
  (byte-identical on re-run), ~7 s, zero model calls.
- `python3 code/evaluation/main.py validate` → VALID (schema, bounds, plan/option match,
  flexible-only spending changes).
- `python3 code/evaluation/main.py samples` → status 24/25, method 24/25, plan 23/25,
  changes 23/25, earliest date 24/25, median |error| of `amount_safe_to_pay` 0.8 % (mean 4.6 %).
  Differences remain on request_06, request_12, request_19.

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
3. **Three-week chains** (`FIRST_GAP = {21: 15}` in `recurrence.py`) reserve their next
   occurrence ~15 days out, then continue every 21 days. Pure-21 and pure-15 both scored worse.
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
   cross-checked against the amount-in-words line. `image_14` is handwritten; its value lives in
   `code/cache/verified_image_amounts.json` and is replaced by a real vision call when
   `ANTHROPIC_API_KEY` is set.

## Known open items / ideas

- request_06 and request_12: the reference answer prefers a spending-change plan where the
  forecast says none is needed (and vice versa) — both are within ~8 % of the balance floor, i.e.
  the variable-spending base estimate is slightly off.
- request_19: reference pays a round 28,820 today rather than the computed 29,732.13; suggests
  the ground truth rounds the partial first payment in some way not yet identified.
- The LLM layer is wired but unexercised; enabling it (`ANTHROPIC_API_KEY`) only affects messages
  the templates cannot classify (currently none) and unreadable images (currently one).
- `code/evaluation/decisions_debug.jsonl` holds per-request series, facts, candidates and notes —
  the fastest way to diagnose a wrong decision.

## Conventions to keep

- No hardcoded labels, no organizer-only files, secrets from environment variables only.
- Every output row passes `planner.verify()` before it is written; a failure falls back to the
  conservative `not_recommended` row and is recorded in `evaluation/run_summary.json`.
- `log.txt` is append-only and must never be committed (see `AGENTS.md` §2 and `.gitignore`).
