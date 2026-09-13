# Token usage and cost report — final full-dataset run

The run that produced `output.csv` and `code/evaluation/llm_audit.md`, as recorded in
`code/evaluation/run_summary.json`:

| Item | Value |
|---|---|
| Command | `python3 code/main.py --llm off --llm-audit` (repo root), dataset `dataset/`, 250 requests; `GEMINI_API_KEY` set, `ANTHROPIC_API_KEY` unset |
| Date | 2026-09-13 (audit finished 02:14 UTC, 07:44 IST) |
| Wall-clock time | 864.4 s: 4.2 s for the 250 decisions, the rest for the audit (requests spaced 4 s apart) |
| Decisions | made without any model call (`--llm off`, `used_for_decisions: false`); `output.csv` is byte-identical to the previous run |
| Verification problems | 0 |
| Contract check | `python3 code/evaluation/main.py validate` → VALID |

## Model calls made by the final run

| Provider | Model | Purpose | Calls | Input tokens | Output tokens | Thinking tokens | Retries |
|---|---|---|---|---|---|---|---|
| Google (Gemini API, REST `generateContent`) | `gemini-3.5-flash-lite` | report-only audit (`--llm-audit`): every row of `messages.csv` read once and compared with the rule-based reading | **215** | **117,388** | **20,583** | 0 | 0 |
| — | tesseract 5 (local OCR, not a hosted model) | amounts for the 16 blank-amount events | 16 images × 3 passes (cached) | n/a | n/a | n/a | n/a |

| Tokens and cost | Whole run | Per request (250) | Per audited message (215) |
|---|---|---|---|
| Input tokens | 117,388 | 469.6 | 546.0 |
| Output tokens | 20,583 | 82.3 | 95.7 |
| Total tokens | 137,971 | 551.9 | 641.7 |
| Cost on the free tier | US$ 0.00 | US$ 0.00 | US$ 0.00 |
| Cost at paid-tier list prices (US$ 0.30 per 1M input tokens, US$ 2.50 per 1M output tokens) | US$ 0.0867 | US$ 0.00035 | US$ 0.00040 |

* The 215 calls were the report-only audit, and no decision depended on a model call: the decisions ran
  with `--llm off`, the audit ran after `output.csv` was written and never changes a reading, and the output is
  byte-identical to the run without a key. Of the 16 images, 14 were read by OCR with the amount-in-words
  cross-check, the handwritten bill (`image_14`) came from `code/cache/verified_image_amounts.json`, and
  `image_04` (item subtotal only) stays unknown.
* Prices are the Standard paid-tier list prices for `gemini-3.5-flash-lite` on the Gemini API pricing page
  (checked 2026-09-13); the free tier is free of charge. The model reported no thinking tokens, and no request
  needed a retry (no 429 or 503) at the 4 s spacing.
* Responses are cached in `code/cache/llm_cache.json` (not committed), so re-running the audit costs nothing.

## Projected usage if decisions may call the model

With `--llm auto` (the default) and a key set, decisions would also send the two images OCR cannot settle,
the subtotal-only receipt (`image_04`) and the handwritten bill (`image_14`), to the vision model: two extra
calls, well under US$ 0.01 at list prices. Everything else in the pipeline is deterministic and needs no model.

## AI assistance during development (not part of the run)

The solution was designed and coded interactively with Anthropic's Claude (Claude Fable 5.1 via claude.ai) and
then tuned in Claude Code (Claude Opus 5): the output comparison tool, the request_19 and weekly-chain
investigations, the estimator and tolerance experiments, the Gemini provider and the audit. That usage is a
development activity, is not metered per call by the runtime, and is therefore not included above; the
transcripts are in `log.txt`.
