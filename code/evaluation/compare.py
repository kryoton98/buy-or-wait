#!/usr/bin/env python3
"""Compare two output.csv files to judge the blast radius of a change.

    python3 code/evaluation/compare.py BEFORE.csv AFTER.csv
    python3 code/evaluation/compare.py <(git show HEAD:output.csv) output.csv

For the request_ids present in both files it prints
  * per column, how many rows differ (exact text), and how many of those differences are
    formatting only (the same numbers written differently, e.g. 620.4 vs 620.40);
  * on how many rows amount_safe_to_pay changed value, and by how much relative to the larger
    of the two values;
  * status and method transition matrices (rows = before, columns = after, "." = no row);
  * every changed request_id with its before -> after values (explanation text is only flagged).
request_ids found in only one of the files are listed first. Exit status: 0 when the comparison
ran, whether or not anything differs; 2 when an input cannot be read.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from statistics import median

STATUSES = ["affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"]
METHODS = ["full_payment", "partial_payment", "installments", "wait", "not_recommended"]
ABBREVIATIONS = {
    "affordable_now": "now", "affordable_with_plan": "with_plan", "affordable_later": "later",
    "not_affordable": "not_aff", "full_payment": "full", "partial_payment": "partial",
    "installments": "install", "wait": "wait", "not_recommended": "not_rec",
}
SHORT_NAMES = {
    "amount_safe_to_pay": "safe", "affordability_status": "status", "recommended_payment_method": "method",
    "payment_plan": "plan", "earliest_date_for_full_payment": "earliest",
    "spending_changes_needed": "changes", "decision_explanation": "explanation",
}
NUMERIC_COLUMNS = ("amount_safe_to_pay", "payment_plan", "spending_changes_needed")
BLANK = "(blank)"

Rows = dict[str, dict[str, str]]


def natural_key(text: str) -> list:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", text)]


def read_output(path: str) -> tuple[list[str], Rows]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        if "request_id" not in header:
            raise ValueError(f"{path}: no request_id column")
        rows: Rows = {}
        duplicates = set()
        for r in reader:
            rid = r["request_id"] or ""
            if rid in rows:
                duplicates.add(rid)
            rows[rid] = {c: r.get(c) or "" for c in header}
    if duplicates:
        raise ValueError(f"{path}: duplicate request_id {', '.join(sorted(duplicates, key=natural_key))}")
    return header, rows


def number(text: str) -> Decimal | str:
    """text as a finite Decimal, or text itself when it is not a number."""
    try:
        value = Decimal(text)
    except InvalidOperation:
        return text
    return value if value.is_finite() else text


def canonical(column: str, text: str) -> object:
    """text with its numbers parsed, so that formatting-only differences compare equal."""
    if column == "amount_safe_to_pay":
        return number(text)
    if column == "payment_plan":
        return [(day, number(amount)) for day, _, amount in (p.partition(":") for p in text.split("|"))]
    if column == "spending_changes_needed":
        return [[number(x) if i == 2 else x for i, x in enumerate(t.split(":"))] for t in text.split("|")]
    return text


def print_amount_shift(ids: list[str], changed: list[str], before: Rows, after: Rows) -> None:
    shifts, not_numbers = [], 0
    for r in changed:
        a, b = number(before[r]["amount_safe_to_pay"]), number(after[r]["amount_safe_to_pay"])
        if not (isinstance(a, Decimal) and isinstance(b, Decimal)):
            not_numbers += 1
        elif a != b:
            shifts.append((abs(b - a) / max(abs(a), abs(b), Decimal(1)), b > a, r))
    line = f"\namount_safe_to_pay changed value on {len(shifts)} of {len(ids)} rows"
    if shifts:
        up = sum(1 for s in shifts if s[1])
        largest = max(shifts, key=lambda s: s[0])
        line += (f" ({up} up, {len(shifts) - up} down); relative change median "
                 f"{float(median(s[0] for s in shifts)):.2%}, max {float(largest[0]):.2%} ({largest[2]})")
    if not_numbers:
        line += f"; {not_numbers} differing rows are blank or not a number in one of the files"
    print(line)


def print_matrix(column: str, order: list[str], ids: list[str], before: Rows, after: Rows) -> None:
    pairs = Counter((before[r][column], after[r][column]) for r in ids)
    values = order + sorted({v for pair in pairs for v in pair} - set(order))
    names = [v or BLANK for v in values] + ["total"]
    heads = [ABBREVIATIONS.get(v, v or BLANK) for v in values] + ["total"]
    table = [[pairs[(a, b)] for b in values] for a in values]
    table = [row + [sum(row)] for row in table]
    table.append([sum(col) for col in zip(*table)])
    corner = "before \\ after"
    name_width = max(len(n) for n in names + [corner])
    widths = [max(len(h), len(str(len(ids)))) for h in heads]
    moved = sum(n for (a, b), n in pairs.items() if a != b)
    print(f"\n{column} transitions: {moved} of {len(ids)} rows changed (rows = before, columns = after)")
    print(f"  {corner:<{name_width}}" + "".join(f"  {h:>{w}}" for h, w in zip(heads, widths)))
    last = len(values)
    for i, (name, row) in enumerate(zip(names, table)):
        cells = [str(n) if n or last in (i, j) else "." for j, n in enumerate(row)]
        print(f"  {name:<{name_width}}" + "".join(f"  {c:>{w}}" for c, w in zip(cells, widths)))


def report(before_header: list[str], before: Rows, after_header: list[str], after: Rows) -> None:
    ids = sorted(before.keys() & after.keys(), key=natural_key)
    columns = [c for c in before_header if c in after_header and c != "request_id"]
    if before_header != after_header:
        print(f"headers differ; comparing the {len(columns)} shared columns\n"
              f"  before: {','.join(before_header)}\n  after:  {','.join(after_header)}")
    for side, rows, other in (("before", before, after), ("after", after, before)):
        only = sorted(rows.keys() - other.keys(), key=natural_key)
        if only:
            print(f"only in {side} ({len(only)}): {', '.join(only)}")

    changed = {c: [r for r in ids if before[r][c] != after[r][c]] for c in columns}
    any_column = sorted({r for rs in changed.values() for r in rs}, key=natural_key)
    decision = {r for c, rs in changed.items() if c != "decision_explanation" for r in rs}
    width = max(len(c) for c in columns + ["any column except decision_explanation"])
    print(f"\nrows that differ, per column (of {len(ids)} shared rows)")
    print(f"  {'column':<{width}}  differ  format-only")
    for c in columns:
        format_only = sum(canonical(c, before[r][c]) == canonical(c, after[r][c]) for r in changed[c])
        print(f"  {c:<{width}}  {len(changed[c]):>6}  {format_only if c in NUMERIC_COLUMNS else '-':>11}")
    print(f"  {'any column':<{width}}  {len(any_column):>6}")
    print(f"  {'any column except decision_explanation':<{width}}  {len(decision):>6}")

    if "amount_safe_to_pay" in columns:
        print_amount_shift(ids, changed["amount_safe_to_pay"], before, after)
    for column, order in (("affordability_status", STATUSES), ("recommended_payment_method", METHODS)):
        if column in columns:
            print_matrix(column, order, ids, before, after)

    if not any_column:
        print("\nno request_id changed")
        return
    print(f"\nchanged request_ids ({len(any_column)}):")
    for r in any_column:
        parts = []
        for c in columns:
            old, new = before[r][c], after[r][c]
            if old == new:
                continue
            if c == "decision_explanation":
                parts.append("explanation: changed")
                continue
            note = " (format only)" if c in NUMERIC_COLUMNS and canonical(c, old) == canonical(c, new) else ""
            parts.append(f"{SHORT_NAMES.get(c, c)}: {old or BLANK} -> {new or BLANK}{note}")
        print(f"  {r}  " + "; ".join(parts))


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare two output.csv files: rows that differ per column, "
                                             "status/method transition matrices and the changed request_ids.")
    ap.add_argument("before", help="baseline output.csv")
    ap.add_argument("after", help="output.csv produced after the change")
    args = ap.parse_args()
    try:
        before_header, before = read_output(args.before)
        after_header, after = read_output(args.after)
    except (OSError, ValueError, csv.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"before: {args.before} ({len(before)} rows)")
    print(f"after:  {args.after} ({len(after)} rows)")
    report(before_header, before, after_header, after)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
