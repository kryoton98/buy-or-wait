#!/usr/bin/env python3
"""Compare two output.csv files: per-column change counts, status transitions, changed ids.

    python3 code/evaluation/compare.py /tmp/baseline_output.csv output.csv
"""
import sys
from collections import Counter

import pandas as pd


def main(a_path: str, b_path: str) -> int:
    a = pd.read_csv(a_path, dtype=str, keep_default_na=False).set_index("request_id")
    b = pd.read_csv(b_path, dtype=str, keep_default_na=False).set_index("request_id")
    ids = [i for i in a.index if i in b.index]
    missing = set(a.index) ^ set(b.index)
    if missing:
        print(f"WARNING: request ids differ between files: {sorted(missing)}")
    cols = [c for c in a.columns if c in b.columns]
    changed = {c: [i for i in ids if a.at[i, c] != b.at[i, c]] for c in cols}
    print(f"{len(ids)} shared rows\n")
    print("rows changed per column:")
    for c in cols:
        print(f"  {c:32s} {len(changed[c]):4d}")
    any_changed = sorted({i for c in cols for i in changed[c]}, key=lambda s: int(s.split("_")[-1]))
    print(f"\nrows with any change: {len(any_changed)}")
    trans = Counter((a.at[i, "affordability_status"], b.at[i, "affordability_status"]) for i in any_changed
                    if a.at[i, "affordability_status"] != b.at[i, "affordability_status"])
    if trans:
        print("\nstatus transitions (old -> new):")
        for (x, y), n in trans.most_common():
            print(f"  {x:22s} -> {y:22s} {n}")
    mtrans = Counter((a.at[i, "recommended_payment_method"], b.at[i, "recommended_payment_method"]) for i in any_changed
                     if a.at[i, "recommended_payment_method"] != b.at[i, "recommended_payment_method"])
    if mtrans:
        print("\nmethod transitions (old -> new):")
        for (x, y), n in mtrans.most_common():
            print(f"  {x:18s} -> {y:18s} {n}")
    if any_changed:
        print("\nchanged request_ids:")
        for i in any_changed:
            diffs = [c for c in cols if a.at[i, c] != b.at[i, c]]
            detail = "; ".join(f"{c}: {a.at[i, c][:40]} -> {b.at[i, c][:40]}" for c in diffs if c != "decision_explanation")
            print(f"  {i}: {detail}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
