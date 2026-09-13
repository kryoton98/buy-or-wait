#!/usr/bin/env python3
"""Compare base-amount estimators for noisy recurring series on the 25 solved samples.

    python3 code/evaluation/experiments/estimators.py [--details]

recurrence._base_amount is monkeypatched with each alternative. Only the estimate for a series whose
amounts vary is replaced; the structural branches stay as they are (a constant series keeps its value,
a level change keeps its new level, a reducible item keeps its minimum-allowed anchor), so every variant
answers the same question with the same inputs (the regular, known amounts of one series).

Variants: the history mean, median, mid-range and mean of the last three amounts, and the noise-interval
estimator over NOISE_WIDE x NOISE_TIGHT. For each the table shows the six sample metrics (exact matches
of status, method, plan, spending changes and earliest date, and amounts within 2 %) plus the mean and
median relative error of amount_safe_to_pay. The run is deterministic and restores the original function
after every variant. The variant that reproduces the current code is starred; if none does,
with_estimator no longer mirrors recurrence._base_amount and a warning is printed.
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CODE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE_ROOT))

import buyorwait.recurrence as rec  # noqa: E402

_spec = importlib.util.spec_from_file_location("evaluation_main", CODE_ROOT / "evaluation" / "main.py")
evaluation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evaluation)

COUNTS = ("status_ok", "method_ok", "plan_ok", "changes_ok", "earliest_ok")
WIDE_GRID = (0.25, 0.28, 0.30)
TIGHT_GRID = (0.10, 0.12, 0.15)
TIGHT_BAND_MAX_RATIO = 1.30  # hi/lo at or below which the noise-interval estimator uses the tight band
ORIGINAL = rec._base_amount


def noise_interval(wide: float, tight: float):
    """Centre of the base interval compatible with the observed spread under an assumed band width."""
    def estimate(a: np.ndarray) -> float:
        lo, hi = float(a.min()), float(a.max())
        width = tight if hi / lo <= TIGHT_BAND_MAX_RATIO else wide
        b_lo, b_hi = hi / (1 + width), lo / (1 - width)
        return (b_lo + b_hi) / 2.0 if b_lo <= b_hi else (lo + hi) / 2.0
    return estimate


ESTIMATORS = {
    "mean": lambda a: float(a.mean()),
    "median": lambda a: float(np.median(a)),
    "midrange": lambda a: (float(a.min()) + float(a.max())) / 2.0,
    "mean of last 3": lambda a: float(a[-3:].mean()),
    **{f"noise W={w:.2f} T={t:.2f}": noise_interval(w, t) for w, t in itertools.product(WIDE_GRID, TIGHT_GRID)},
}


def with_estimator(estimate):
    """recurrence._base_amount with the noisy-series estimate replaced and the structural branches kept."""
    def base_amount(cat, amounts, flexibility, min_allowed):
        a = np.array(amounts, dtype=float)
        if len(a) == 0 or a.max() - a.min() < 1e-9 or len(set(np.round(a, 6))) <= max(2, len(a) // 2):
            return ORIGINAL(cat, amounts, flexibility, min_allowed)  # empty, constant or level change
        if flexibility in ("reducible", "reducible_or_stoppable") and min_allowed and not pd.isna(min_allowed):
            return ORIGINAL(cat, amounts, flexibility, min_allowed)  # minimum-allowed anchor
        return estimate(a)
    return base_amount


def summary(df: pd.DataFrame) -> dict:
    row = {k: int(df[k].sum()) for k in COUNTS}
    row.update(within=int(df.safe_within_2pct.sum()), mean=float(df.safe_rel_err.mean()), median=float(df.safe_rel_err.median()))
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare base-amount estimators on the 25 solved samples.")
    ap.add_argument("--dataset", default=str(CODE_ROOT.parent / "dataset"))
    ap.add_argument("--details", action="store_true", help="list the samples whose decision differs from the current code")
    args = ap.parse_args()

    current = evaluation.score_samples(args.dataset).set_index("request_id")
    results = {}
    for name, estimate in ESTIMATORS.items():
        rec._base_amount = with_estimator(estimate)
        try:
            results[name] = evaluation.score_samples(args.dataset).set_index("request_id")
        finally:
            rec._base_amount = ORIGINAL
    matching = {name for name, df in results.items() if df.got.equals(current.got)}

    base = summary(current)
    print(f"{'estimator':22s} {'status':>6s} {'method':>6s} {'plan':>4s} {'changes':>7s} {'earliest':>8s} {'within 2%':>9s} {'mean err':>8s} {'median err':>10s}")
    for name, df in results.items():
        s = summary(df)
        label = name + (" *" if name in matching else "")
        print(f"{label:22s} {s['status_ok']:>6d} {s['method_ok']:>6d} {s['plan_ok']:>4d} {s['changes_ok']:>7d} "
              f"{s['earliest_ok']:>8d} {s['within']:>9d} {s['mean']:>8.2%} {s['median']:>10.2%}")
    if matching:
        print(f"(* reproduces the current code; {len(current)} samples)")
    else:
        print("warning: no variant reproduces the current code; with_estimator may no longer mirror "
              "recurrence._base_amount", file=sys.stderr)

    better = [name for name, df in results.items()
              if summary(df)["median"] < base["median"] - 1e-12 and all(summary(df)[k] >= base[k] for k in COUNTS)]
    print("\nlower median error than the current code with no exact-match count below it: " + (", ".join(better) if better else "none"))

    if args.details:
        for name, df in results.items():
            changed = [rid for rid in df.index if df.at[rid, "got"] != current.at[rid, "got"]]
            print(f"\n{name}: {len(changed)} sample decision(s) differ from the current code")
            for rid in changed:
                flips = [k[:-3] + ("+" if df.at[rid, k] else "-") for k in COUNTS if df.at[rid, k] != current.at[rid, k]]
                print(f"  {rid}: {','.join(flips) or 'amount only'}; error "
                      f"{current.at[rid, 'safe_rel_err']:.2%} -> {df.at[rid, 'safe_rel_err']:.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
