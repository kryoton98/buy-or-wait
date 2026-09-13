#!/usr/bin/env python3
"""Buy or Wait? — run the agent over dataset/requests.csv and write output.csv.

Usage:
    python code/main.py                       # full run: writes <repo>/output.csv
    python code/main.py --requests samples    # decide the 25 solved samples only
    python code/main.py --llm off             # decisions never call a model
    python code/main.py --llm-audit           # also read every message with the model -> evaluation/llm_audit.md

The model provider is Anthropic when ANTHROPIC_API_KEY is set, otherwise Google Gemini when GEMINI_API_KEY is set.
Everything is read from the dataset folder; secrets come from environment variables only.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CODE_ROOT))

from buyorwait.llm_audit import run_llm_audit, write_audit_report  # noqa: E402
from buyorwait.pipeline import Agent  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Buy or Wait? financial decision agent")
    ap.add_argument("--dataset", default=str(CODE_ROOT.parent / "dataset"), help="path to the dataset folder")
    ap.add_argument("--output", default=str(CODE_ROOT.parent / "output.csv"), help="where to write output.csv")
    ap.add_argument("--requests", choices=["all", "samples"], default="all")
    ap.add_argument("--llm", choices=["auto", "off"], default="auto",
                    help="auto = decisions may ask the model about evidence the rules cannot read, when an API key is set")
    ap.add_argument("--llm-audit", action="store_true",
                    help="after deciding, read every message with the model and write evaluation/llm_audit.md "
                         "(never changes a decision)")
    ap.add_argument("--debug-jsonl", default=str(CODE_ROOT / "evaluation" / "decisions_debug.jsonl"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    agent = Agent(args.dataset, CODE_ROOT, use_llm=args.llm, verbose=not args.quiet)
    if args.llm_audit and not agent.llm.enabled:
        print("--llm-audit needs ANTHROPIC_API_KEY or GEMINI_API_KEY in the environment", file=sys.stderr)
        return 2
    if not args.quiet:
        print(f"dataset loaded from {args.dataset}: {len(agent.ds.requests)} requests, {len(agent.ds.events)} events, "
              f"{len(agent.ds.messages)} messages, {len(agent.ds.images)} images")
    agent.read_all_evidence()
    if not args.quiet:
        kinds = {}
        for rd in agent.readings.values():
            kinds[rd.kind] = kinds.get(rd.kind, 0) + 1
        print(f"evidence: {len(agent.readings)} messages classified ({kinds.get('unknown', 0)} unknown), "
              f"{len(agent.image_amounts)}/{len(agent.ds.images)} image amounts read; model {agent.llm.provider} "
              f"{agent.llm.model}: key {'set' if agent.llm.enabled else 'not set'}, used for decisions={agent.use_llm}")
    requests = agent.ds.sample_requests if args.requests == "samples" else agent.ds.requests
    out = agent.run(requests, Path(args.debug_jsonl))
    out.to_csv(args.output, index=False)
    decision_seconds = round(time.time() - t0, 1)

    audit = None
    if args.llm_audit:
        # runs after output.csv is written and never touches agent.readings, so it cannot change a decision
        results = run_llm_audit(agent, verbose=not args.quiet)
        audit = write_audit_report(results, CODE_ROOT / "evaluation" / "llm_audit.md", agent.usage())
        audit["report"] = "code/evaluation/llm_audit.md"

    usage = agent.usage()
    summary = {
        "requests": int(len(out)), "output": args.output, "seconds": round(time.time() - t0, 1),
        "decision_seconds": decision_seconds, "verification_problems": agent.problems,
        "llm": dict(usage, used_for_decisions=agent.use_llm), "llm_audit": audit,
        "image_evidence": {eid: {"amount": agent.image_amounts.get(eid), "how": agent.image_notes.get(eid)} for eid in agent.image_notes},
        "status_counts": out.affordability_status.value_counts().to_dict(),
        "method_counts": out.recommended_payment_method.value_counts().to_dict(),
    }
    (CODE_ROOT / "evaluation" / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    if not args.quiet:
        print(f"wrote {len(out)} rows to {args.output} in {decision_seconds}s; "
              f"statuses={summary['status_counts']}; verification problems={len(agent.problems)}")
        print(f"model {usage['provider']} {usage['model']}: calls={usage['calls']} cache hits={usage['cache_hits']} "
              f"input tokens={usage['input_tokens']} output tokens={usage['output_tokens']}"
              + (f"; audit {audit['agree']} agree, {audit['disagree']} disagree, {audit['no_reading']} without a reading "
                 f"-> {audit['report']}" if audit else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
