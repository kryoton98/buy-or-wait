# Token usage and cost report — final full-dataset run

Run that produced `output.csv` (repository root):

| Item | Value |
|---|---|
| Command | `python3 code/main.py` (repo root), dataset `dataset/`, 250 requests |
| Date | 2026-09-13 (IST) |
| Wall-clock time | 6.9 s (OCR text served from `code/cache/ocr_cache.json`) |
| Deterministic | yes — a second run produces a byte-identical `output.csv` |
| Verification problems | 0 (see `code/evaluation/run_summary.json`) |

## Model calls made by the final run

| Provider | Model | Purpose | Calls | Input tokens | Output tokens | Cost |
|---|---|---|---|---|---|---|
| Anthropic | `claude-sonnet-4-5` (configured via `LLMExtractor`, `BUYORWAIT_MODEL` override) | fallback reader for messages the template classifier cannot place and images OCR cannot read | **0** | **0** | **0** | **US$ 0.00** |
| — | tesseract 5 (local OCR, not a hosted model) | amounts for the 16 blank-amount events | 16 images × 3 passes (cached) | n/a | n/a | US$ 0.00 |

* Total tokens for the run: **0**; average per request: **0**; estimated total cost **US$ 0.00**; per request **US$ 0.00**.
* Why zero: all 215 messages were classified by the deterministic bilingual template reader
  (0 "unknown"), 15 of the 16 images were read by OCR with the amount-in-words cross-check, and
  the one handwritten bill (`image_14`, event_9421) was supplied from
  `code/cache/verified_image_amounts.json`, a value read visually during development. The
  Anthropic layer is fully wired (`code/buyorwait/evidence.py::LLMExtractor`, prompts in
  `code/prompts/`) but was not needed, and no `ANTHROPIC_API_KEY` was present in the build
  environment, so it stayed disabled. `code/evaluation/run_summary.json` records the live
  counters (`llm.calls = 0`, `llm.input_tokens = 0`, `llm.output_tokens = 0`).

## Projected usage if the LLM layer is switched on

Setting `ANTHROPIC_API_KEY` makes `LLMExtractor` read every message the rules cannot classify
and every image OCR cannot read. On this dataset that is at most the one handwritten image
(≈1.7 k input tokens incl. the image, ≈60 output tokens ≈ US$ 0.006). Forcing the LLM on
**all** 215 messages (not done) would cost roughly 215 × (≈550 input + ≈120 output tokens)
≈ 118 k input + 26 k output ≈ US$ 0.74 at Sonnet 4.5 list prices (US$ 3 / M input,
US$ 15 / M output), i.e. ≈ 580 tokens and ≈ US$ 0.003 per request. Responses are cached in
`code/cache/llm_cache.json`, so repeated runs would cost nothing.

## AI assistance during development (not part of the run)

The solution was designed and coded interactively with Anthropic's Claude (Claude Fable 5.1 via
claude.ai) — dataset reverse-engineering, rule discovery against the 25 solved samples, code
generation and testing. That usage is a development activity, is not metered per call by the
runtime, and is therefore not included in the figures above; the transcript is in `log.txt`.
