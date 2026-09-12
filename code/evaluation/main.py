#!/usr/bin/env python3
"""Evaluation utilities for the Buy or Wait? agent.

    python code/evaluation/main.py validate  [--output output.csv]   # schema / contract checks
    python code/evaluation/main.py samples                           # decide the 25 samples and score them

The sample score mirrors the public scoring axes: amount_safe_to_pay accuracy,
affordability_status, recommended_payment_method + payment_plan,
earliest_date_for_full_payment, spending_changes_needed validity.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))

from buyorwait.data import OUTPUT_COLUMNS, load_dataset  # noqa: E402
from buyorwait.pipeline import Agent  # noqa: E402

STATUSES = {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
METHODS = {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}
PLAN_RE = re.compile(r"^\d{4}-\d{2}-\d{2}:\d+(\.\d{1,2})?$")
CHANGE_RE = re.compile(r"^(stop:event_\d+|reduce_to:event_\d+:\d+(\.\d{1,2})?)$")


def validate(output_path: str, dataset_root: str) -> list[str]:
    ds = load_dataset(dataset_root)
    out = pd.read_csv(output_path, dtype=str, keep_default_na=False)
    errs = []
    if list(out.columns) != OUTPUT_COLUMNS:
        errs.append(f"columns differ: {list(out.columns)}")
    req = ds.requests.set_index("request_id")
    if set(out.request_id) != set(req.index) or len(out) != len(req):
        errs.append(f"request ids mismatch: {len(out)} rows vs {len(req)} requests")
    opts = ds.options
    events = ds.events.set_index("event_id")
    for _, r in out.iterrows():
        rid = r.request_id
        if rid not in req.index:
            continue
        q = req.loc[rid]
        try:
            safe = float(r.amount_safe_to_pay)
        except ValueError:
            errs.append(f"{rid}: amount_safe_to_pay not numeric")
            continue
        if not (0 <= safe <= float(q.requested_amount) + 1e-6):
            errs.append(f"{rid}: amount_safe_to_pay {safe} outside [0, {q.requested_amount}]")
        if r.affordability_status not in STATUSES:
            errs.append(f"{rid}: bad status {r.affordability_status}")
        if r.recommended_payment_method not in METHODS:
            errs.append(f"{rid}: bad method {r.recommended_payment_method}")
        plan = r.payment_plan
        if r.recommended_payment_method == "not_recommended":
            if plan != "none" or r.affordability_status != "not_affordable":
                errs.append(f"{rid}: not_recommended must have plan none / not_affordable")
        else:
            parts = plan.split("|")
            if not all(PLAN_RE.match(p) for p in parts):
                errs.append(f"{rid}: malformed plan {plan}")
            else:
                dates = [p.split(":")[0] for p in parts]
                if dates != sorted(dates):
                    errs.append(f"{rid}: plan not chronological")
                total = sum(float(p.split(":")[1]) for p in parts)
                if r.recommended_payment_method in ("full_payment", "wait", "partial_payment") and abs(total - float(q.requested_amount)) > 0.011:
                    errs.append(f"{rid}: plan total {total} != requested {q.requested_amount}")
                if r.recommended_payment_method == "partial_payment" and (len(parts) != 2 or abs(float(parts[0].split(':')[1]) - safe) > 0.011):
                    errs.append(f"{rid}: partial plan must be safe-today + remainder")
                if r.recommended_payment_method == "installments":
                    o = opts[(opts.request_id == rid) & (opts.payment_method == "installments")]
                    ok = False
                    for _, x in o.iterrows():
                        n = int(x.number_of_payments)
                        exp = [(x.first_payment_date + pd.Timedelta(days=int(x.payment_frequency_days) * k)).isoformat() for k in range(n)]
                        if exp == dates and all(abs(float(p.split(":")[1]) - float(x.payment_amount)) < 0.011 for p in parts):
                            ok = True
                    if not ok:
                        errs.append(f"{rid}: installment plan does not match a supplied option")
        if r.affordability_status == "affordable_now":
            if r.earliest_date_for_full_payment != q.request_date.isoformat():
                errs.append(f"{rid}: affordable_now requires earliest == request_date")
            if r.recommended_payment_method != "full_payment":
                errs.append(f"{rid}: affordable_now requires full_payment")
        if r.earliest_date_for_full_payment and not re.match(r"^\d{4}-\d{2}-\d{2}$", r.earliest_date_for_full_payment):
            errs.append(f"{rid}: bad earliest date")
        if r.spending_changes_needed != "none":
            toks = r.spending_changes_needed.split("|")
            if len(toks) > 3:
                errs.append(f"{rid}: more than 3 spending changes")
            seen = set()
            for t in toks:
                if not CHANGE_RE.match(t):
                    errs.append(f"{rid}: malformed change {t}")
                    continue
                eid = t.split(":")[1]
                if eid in seen:
                    errs.append(f"{rid}: stop and reduce on the same event")
                seen.add(eid)
                if eid not in events.index:
                    errs.append(f"{rid}: unknown event {eid}")
                    continue
                ev = events.loc[eid]
                if ev.user_id != q.user_id:
                    errs.append(f"{rid}: change targets another user's event")
                flex = str(ev.flexibility)
                if t.startswith("stop") and flex not in ("stoppable", "reducible_or_stoppable"):
                    errs.append(f"{rid}: {eid} is not stoppable")
                if t.startswith("reduce_to"):
                    if flex not in ("reducible", "reducible_or_stoppable"):
                        errs.append(f"{rid}: {eid} is not reducible")
                    elif not pd.isna(ev.minimum_allowed_amount) and float(t.split(":")[2]) < float(ev.minimum_allowed_amount) - 0.011:
                        errs.append(f"{rid}: reduce below the minimum allowed amount")
        if not r.decision_explanation.strip():
            errs.append(f"{rid}: empty explanation")
    return errs


def score_samples(dataset_root: str) -> pd.DataFrame:
    agent = Agent(dataset_root, CODE_ROOT, use_llm="off", verbose=False)
    agent.read_all_evidence()
    out = agent.run(agent.ds.sample_requests)
    exp = agent.ds.sample_requests.set_index("request_id")
    rows = []
    for _, r in out.iterrows():
        e = exp.loc[r.request_id]
        exp_e = "" if isinstance(e.earliest_date_for_full_payment, float) else str(e.earliest_date_for_full_payment)[:10]
        exp_safe = float(e.amount_safe_to_pay)
        got_safe = float(r.amount_safe_to_pay)
        rows.append({
            "request_id": r.request_id,
            "status_ok": r.affordability_status == e.affordability_status,
            "method_ok": r.recommended_payment_method == e.recommended_payment_method,
            "plan_ok": r.payment_plan == str(e.payment_plan),
            "changes_ok": r.spending_changes_needed == str(e.spending_changes_needed),
            "earliest_ok": r.earliest_date_for_full_payment == exp_e,
            "safe_rel_err": abs(got_safe - exp_safe) / max(exp_safe, 1.0),
            "safe_within_2pct": abs(got_safe - exp_safe) <= 0.02 * max(exp_safe, 1.0),
            "got": f"{r.affordability_status}/{r.recommended_payment_method}/{r.payment_plan}/{r.spending_changes_needed}/{r.earliest_date_for_full_payment}/{r.amount_safe_to_pay}",
            "expected": f"{e.affordability_status}/{e.recommended_payment_method}/{e.payment_plan}/{e.spending_changes_needed}/{exp_e}/{e.amount_safe_to_pay}",
        })
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["validate", "samples"])
    ap.add_argument("--dataset", default=str(CODE_ROOT.parent / "dataset"))
    ap.add_argument("--output", default=str(CODE_ROOT.parent / "output.csv"))
    args = ap.parse_args()
    if args.mode == "validate":
        errs = validate(args.output, args.dataset)
        print(f"{args.output}: {'VALID' if not errs else str(len(errs)) + ' problem(s)'}")
        for e in errs[:50]:
            print("  -", e)
        return 0 if not errs else 1
    df = score_samples(args.dataset)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 120)
    for _, r in df.iterrows():
        flags = [k.replace("_ok", "") for k in ("status_ok", "method_ok", "plan_ok", "changes_ok", "earliest_ok", "safe_within_2pct") if not r[k]]
        print(f"{r.request_id}: {'OK' if not flags else 'differs on ' + ','.join(flags)}")
        if flags:
            print(f"    got      {r.got}\n    expected {r.expected}")
    n = len(df)
    print("\nsample score:")
    for k in ("status_ok", "method_ok", "plan_ok", "changes_ok", "earliest_ok", "safe_within_2pct"):
        print(f"  {k:18s} {int(df[k].sum())}/{n}")
    print(f"  mean |rel err| of amount_safe_to_pay: {df.safe_rel_err.mean():.2%}   median: {df.safe_rel_err.median():.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
