"""LLM audit of the rule-based message reader: a report only, never an input to decisions.

``python3 code/main.py --llm-audit`` runs this after output.csv is written: every row of messages.csv is read by
the configured model, the reading is validated exactly like the decision-time fallback, and its kind and facts
are compared with the template reading. The result is written to evaluation/llm_audit.md.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from .evidence import _parse_json, llm_message_reading


def canonical_facts(facts: list[dict]) -> list[tuple]:
    """Comparable form of a fact list: type, amount to the cent, currency, date, scope, multiplier (labels ignored)."""
    out = []
    for f in facts:
        day = f.get("date")
        out.append((
            str(f.get("type")),
            None if f.get("amount") is None else round(float(f["amount"]), 2),
            str(f["currency"]).upper() if f.get("currency") else None,
            day.isoformat() if isinstance(day, date) else (str(day)[:10] if day else None),
            str(f["scope"]) if f.get("scope") else None,
            None if f.get("multiplier") is None else round(float(f["multiplier"]), 4),
        ))
    return sorted(out, key=repr)


def describe_facts(facts: list[dict]) -> str:
    if not facts:
        return "no facts"
    parts = []
    for kind, amount, currency, day, scope, multiplier in canonical_facts(facts):
        bits = [kind]
        if amount is not None:
            bits.append(f"{amount:,.2f}".rstrip("0").rstrip(".") + (f" {currency}" if currency else ""))
        if day:
            bits.append(day)
        if scope:
            bits.append(f"scope={scope}")
        if multiplier is not None:
            bits.append(f"x{multiplier}")
        parts.append(" ".join(bits))
    return "; ".join(parts)


def run_llm_audit(agent, verbose: bool = False) -> list[dict]:
    """Read every message with the model and compare it with the rule-based reading already in ``agent.readings``."""
    results = []
    messages = agent.ds.messages
    for i, (_, row) in enumerate(messages.iterrows(), 1):
        rule = agent.readings[row.message_id]
        entry = {"message_id": str(row.message_id), "user_id": str(row.user_id), "text": str(row.message_text),
                 "rule_kind": rule.kind, "rule_facts": rule.facts, "llm_kind": None, "llm_facts": [],
                 "llm_summary": "", "differs": [], "note": ""}
        try:
            raw = agent.llm.read_message_text(entry["text"])
        except Exception as exc:  # an API error that survived the retries is recorded and the audit goes on
            raw = None
            entry["note"] = f"call failed: {type(exc).__name__} {getattr(exc, 'code', '')}".strip()
        reading = llm_message_reading(entry["message_id"], _parse_json(raw)) if raw else None
        if reading is None:
            entry["result"] = "no reading"
            if not entry["note"]:
                entry["note"] = ("output is not JSON with a known kind: " + " ".join(raw.split())[:160]) if raw else "no model output"
        else:
            entry.update(llm_kind=reading.kind, llm_facts=reading.facts, llm_summary=reading.summary)
            entry["differs"] = [name for name, same in (("kind", reading.kind == rule.kind),
                                                        ("facts", canonical_facts(reading.facts) == canonical_facts(rule.facts)))
                                if not same]
            entry["result"] = "disagree" if entry["differs"] else "agree"
        results.append(entry)
        if verbose and i % 25 == 0:
            print(f"  {i}/{len(messages)} messages audited")
    return results


def write_audit_report(results: list[dict], path: Path, usage: dict) -> dict:
    counts = {"messages": len(results),
              "agree": sum(r["result"] == "agree" for r in results),
              "disagree": sum(r["result"] == "disagree" for r in results),
              "no_reading": sum(r["result"] == "no reading" for r in results)}
    lines = [
        "# LLM audit of the message readings",
        "",
        "Every row of `dataset/messages.csv` was read twice: by the rule-based template reader that the decisions use",
        "(`code/buyorwait/evidence.py`) and by the model below, whose output is validated like the decision-time",
        "fallback. This is a report only: no decision and no row of `output.csv` depends on it. Readings agree when the",
        "template kind and the facts match (type, amount to the cent, currency, date, scope and multiplier; labels and",
        "summaries are ignored).",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| Provider and model | {usage.get('provider')} `{usage.get('model')}` |",
        f"| Run | {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC |",
        f"| Messages | {counts['messages']} |",
        f"| Agree / disagree / no usable model reading | {counts['agree']} / {counts['disagree']} / {counts['no_reading']} |",
        f"| Model calls (cache hits) | {usage.get('calls', 0)} ({usage.get('cache_hits', 0)}) |",
        f"| Input / output tokens | {usage.get('input_tokens', 0):,} / {usage.get('output_tokens', 0):,} |",
        "",
        "## Disagreements",
        "",
    ]
    differing = [r for r in results if r["result"] != "agree"]
    if not differing:
        lines += ["None.", ""]
    for r in differing:
        lines += [f"### {r['message_id']} ({r['user_id']}): {', '.join(r['differs']) or r['result']}", "",
                  "> " + _one_line(r["text"], 500), "",
                  f"- rules: `{r['rule_kind']}`; {describe_facts(r['rule_facts'])}"]
        if r["llm_kind"]:
            summary = f" ({_one_line(r['llm_summary'], 200)})" if r["llm_summary"] else ""
            lines.append(f"- model: `{r['llm_kind']}`; {describe_facts(r['llm_facts'])}{summary}")
        else:
            lines.append(f"- model: no usable reading ({r['note']})")
        lines.append("")
    lines += ["## Every message", "", "| message_id | user_id | result | rules kind | model kind |", "|---|---|---|---|---|"]
    for r in results:
        result = r["result"] + (f" ({', '.join(r['differs'])})" if r["differs"] else "")
        lines.append(f"| {r['message_id']} | {r['user_id']} | {result} | {r['rule_kind']} | {r['llm_kind'] or '-'} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return counts


def _one_line(text: str, limit: int) -> str:
    text = " ".join(str(text).split()).replace("|", "/")
    return text if len(text) <= limit else text[: limit - 1] + "…"
