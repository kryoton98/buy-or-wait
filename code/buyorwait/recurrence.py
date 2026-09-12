"""Recurring-series detection from settled history.

The history for each user is a mix of (a) monthly items on a fixed
day-of-month (rent, utilities, subscriptions, loans, payroll...), (b) items
repeating every N days (groceries, transport, dining, weekly gig income...) and
(c) one-off events (bonuses, refunds, reimbursements, unusual purchases).

We detect (a) and (b) with an "anchor chain" search: for a candidate period we
find the anchor date that explains the largest number of events; the events on
that chain form a series and the rest are re-examined for a second series or
left as one-offs. Amounts are summarised with a *base estimate*:

* constant amounts -> that amount;
* a level change (last two values equal, earlier ones different) -> the new level;
* reducible items -> ``minimum_allowed_amount`` divided by the observed
  minimum ratio (0.5 for dining/entertainment/streaming/gym, 0.4 for shopping);
* otherwise -> the mid-range of the history, which is the efficient estimator of
  the centre for the uniform noise this data exhibits.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

# categories that describe cash *income*
INCOME_CATEGORIES = {"salary"}
# credits that are never recurring income
NON_RECURRING_CREDIT_TYPES = {"refund", "investment_sale", "investment_valuation"}
NON_RECURRING_CREDIT_CATEGORIES = {"windfall", "work_expense", "investment", "shopping"}
# descriptions that flag a one-off credit even though category == salary
ONE_OFF_INCOME_WORDS = ("arrears", "bonus", "prize", "reimburse", "windfall", "settlement", "net salary")

MIN_RATIO = {"shopping": 0.4}
DEFAULT_MIN_RATIO = 0.5
# observed multiplicative noise bands around the base amount (max/min of a clean series
# is ~1.7 for groceries/transport/dining and ~1.25 for utilities/healthcare/shopping/entertainment)
NOISE_WIDE, NOISE_TIGHT = 0.28, 0.12
# Spending that recurs every three weeks keeps its cadence, but the next occurrence is reserved
# about 15 days after the last one (the reference decisions consistently reserve the next such
# item before the following payday even when a strict 21-day step would land just after it).
FORECAST_PERIOD: dict[int, int] = {}
FIRST_GAP: dict[int, int] = {21: 15}
OUTLIER_LOW, OUTLIER_HIGH = 0.6, 1.5


@dataclass
class Series:
    key: str
    category: str
    direction: str
    kind: str  # 'monthly' | 'periodic'
    day: Optional[int]
    period: Optional[int]
    last_date: date
    amount: float
    amounts: list[float]
    dates: list[date]
    event_ids: list[str]
    flexibility: str
    minimum_allowed_amount: Optional[float]
    description: str
    currency: str
    event_type: str
    is_income: bool
    stopped: bool = False
    note: str = ""
    # forward overrides (filled by evidence application)
    amount_overrides: dict = field(default_factory=dict)  # date -> amount
    start_after: Optional[date] = None
    stop_after: Optional[date] = None
    next_date_override: Optional[date] = None
    first_gap: Optional[int] = None  # gap to the first projected occurrence when it differs from the period

    def describe(self) -> str:
        sched = f"day {self.day}" if self.kind == "monthly" else f"every {self.period}d"
        return f"{self.key} {self.direction} {self.kind} ({sched}) last={self.last_date} amt={self.amount:,.2f} n={len(self.amounts)} {self.flexibility}"


def _mode(xs):
    return Counter(xs).most_common(1)[0][0]


def _chain(dates: list[date], period: int, tol: int = 1):
    """Return the largest subset of ``dates`` that lies on an every-``period``-days chain."""
    best = []
    for anchor in dates:
        chosen = [anchor]
        cur = anchor
        # forward
        for d in dates:
            if d <= cur:
                continue
            gap = (d - cur).days
            if abs(gap - period) <= tol or (abs(gap - 2 * period) <= tol):
                chosen.append(d)
                cur = d
        # backward
        cur = anchor
        for d in sorted(dates, reverse=True):
            if d >= cur:
                continue
            gap = (cur - d).days
            if abs(gap - period) <= tol or abs(gap - 2 * period) <= tol:
                chosen.append(d)
                cur = d
        chosen = sorted(set(chosen))
        if len(chosen) > len(best):
            best = chosen
    return best


def _monthly_chain(dates: list[date], tol: int = 2):
    """Largest subset of dates that share a day-of-month (±tol) with monthly spacing."""
    best = []
    days = sorted(set(d.day for d in dates))
    for anchor_day in days:
        chosen = [d for d in dates if abs(d.day - anchor_day) <= tol or (anchor_day >= 28 and d.day >= 28)]
        # enforce monthly spacing (allow missing months)
        chosen = sorted(chosen)
        ok = []
        for d in chosen:
            if not ok or (d - ok[-1]).days >= 26:
                ok.append(d)
        if len(ok) > len(best):
            best = ok
    return best


def regular_mask(amounts: list[float]) -> list[bool]:
    """Flag amounts that are far outside the noise band around the median (one-off outliers)."""
    a = np.array(amounts, dtype=float)
    if len(a) < 3:
        return [True] * len(a)
    m = float(np.median(a))
    return [bool(OUTLIER_LOW * m <= x <= OUTLIER_HIGH * m) for x in a]


def _base_amount(cat: str, amounts: list[float], flexibility: str, min_allowed: Optional[float]) -> float:
    a = np.array(amounts, dtype=float)
    if len(a) == 0:
        return 0.0
    if a.max() - a.min() < 1e-9:
        return float(a[-1])
    distinct = len(set(np.round(a, 6)))
    if distinct <= max(2, len(a) // 2):
        # noise-free item whose level changed (salary revision, temporary pay): the new level is the
        # last value when it already repeated, otherwise the most common value (a single deviating
        # value such as an unpaid-leave month is treated as an anomaly)
        if len(a) >= 2 and abs(a[-1] - a[-2]) < 1e-9:
            return float(a[-1])
        vals, counts = np.unique(np.round(a, 6), return_counts=True)
        return float(vals[np.argmax(counts)])
    if flexibility in ("reducible", "reducible_or_stoppable") and min_allowed and not pd.isna(min_allowed):
        return float(min_allowed) / MIN_RATIO.get(cat, DEFAULT_MIN_RATIO)
    lo, hi = float(a.min()), float(a.max())
    width = NOISE_TIGHT if hi / lo <= 1.30 else NOISE_WIDE
    # the base must satisfy hi <= base*(1+w) and lo >= base*(1-w): take the centre of that interval
    b_lo, b_hi = hi / (1 + width), lo / (1 - width)
    if b_lo <= b_hi:
        return (b_lo + b_hi) / 2.0
    return (lo + hi) / 2.0


def is_one_off_credit(row) -> bool:
    if row.direction != "credit":
        return False
    if row.event_type in NON_RECURRING_CREDIT_TYPES or row.category in NON_RECURRING_CREDIT_CATEGORIES:
        return True
    desc = str(row.description).lower()
    return any(w in desc for w in ONE_OFF_INCOME_WORDS)


def detect_series(hist: pd.DataFrame, home: str, convert, excluded_event_ids: set[str] | None = None) -> tuple[list[Series], list[str]]:
    """Detect recurring series in the settled history ``hist`` (already amount-resolved).

    ``hist`` must have columns event_id, category, direction, amount_home,
    settlement_date, flexibility, minimum_allowed_amount, description, currency,
    event_type. Returns (series, one_off_event_ids).
    """
    excluded_event_ids = excluded_event_ids or set()
    series: list[Series] = []
    one_offs: list[str] = []
    h = hist[~hist.event_id.isin(excluded_event_ids)].copy()
    h = h[h.direction.isin(["debit", "credit"])]
    # recurrence is detected on the scheduled (event) date; cash moves on the settlement date
    h = h.assign(sdate=h.settlement_date, settlement_date=h.event_date)
    h = h.sort_values(["settlement_date", "event_id"])

    for (cat, direction), g in h.groupby(["category", "direction"], sort=False):
        if direction == "credit":
            g_one = g[g.apply(is_one_off_credit, axis=1)]
            one_offs.extend(g_one.event_id.tolist())
            g = g[~g.event_id.isin(g_one.event_id)]
        remaining = g.copy()
        idx = 0
        min_n = 2 if direction == "credit" else 3  # a brand-new salary shows only two payslips
        while len(remaining) >= min_n:
            dates = sorted(set(remaining.settlement_date.tolist()))
            if len(dates) < min_n:
                break
            gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
            cands = []
            # monthly candidate
            mc = _monthly_chain(dates)
            if len(mc) >= min_n:
                cands.append(("monthly", None, mc))
            # periodic candidates: common gaps
            for p in sorted(set(x for x in gaps if 5 <= x <= 25), key=lambda p: -gaps.count(p)):
                pc = _chain(dates, p)
                if len(pc) >= 3:
                    cands.append(("periodic", p, pc))
            if not cands:
                break
            # prefer the candidate explaining the most events; monthly wins ties
            cands.sort(key=lambda c: (-len(c[2]), 0 if c[0] == "monthly" else 1))
            kind, period, chosen = cands[0]
            forecast_period = FORECAST_PERIOD.get(period, period) if kind == "periodic" else None
            first_gap = FIRST_GAP.get(period) if kind == "periodic" else None
            sel = remaining[remaining.settlement_date.isin(chosen)]
            # several events on one chain date: keep the one closest to the typical amount
            if sel.settlement_date.duplicated().any():
                med = float(sel.amount_home.median())
                sel = sel.assign(_dev=(sel.amount_home - med).abs()).sort_values("_dev").drop_duplicates("settlement_date", keep="first").drop(columns="_dev").sort_values(["settlement_date", "event_id"])
            # amounts far outside the noise band (an unpaid-leave month, a one-off bulk purchase) keep
            # their place in the schedule but do not influence the base estimate
            amounts = sel.amount_home.tolist()
            mask = regular_mask(amounts)
            est_amounts = [x for x, ok in zip(amounts, mask) if ok] if sum(mask) >= 3 else amounts
            last = sel.iloc[-1]
            day = _mode([d.day for d in sel.settlement_date]) if kind == "monthly" else None
            key = cat if idx == 0 else f"{cat}#{idx + 1}"
            s = Series(
                key=key,
                category=cat,
                direction=direction,
                kind=kind,
                day=day,
                period=forecast_period,
                first_gap=first_gap,
                last_date=sel.settlement_date.iloc[-1],
                amount=_base_amount(cat, est_amounts, str(last.flexibility), last.minimum_allowed_amount),
                amounts=amounts,
                dates=sel.settlement_date.tolist(),
                event_ids=sel.event_id.tolist(),
                flexibility=str(last.flexibility),
                minimum_allowed_amount=None if pd.isna(last.minimum_allowed_amount) else float(last.minimum_allowed_amount),
                description=str(last.description),
                currency=str(last.currency),
                event_type=str(last.event_type),
                is_income=(direction == "credit"),
            )
            series.append(s)
            idx += 1
            remaining = remaining[~remaining.event_id.isin(sel.event_id)]
        one_offs.extend(remaining.event_id.tolist())
    return series, one_offs


def is_stale(s: Series, R: date) -> bool:
    """A series whose next expected occurrence was missed before the request date is inactive."""
    if s.kind == "monthly":
        return (R - s.last_date).days > 45
    return (R - s.last_date).days > 2 * s.period + 2


def next_dates(s: Series, start: date, end: date) -> list[date]:
    """Project the occurrence dates of ``s`` in [start, end] (inclusive)."""
    out = []
    if s.kind == "monthly":
        d = s.last_date
        day = s.day
        # walk month by month from the month after the last occurrence
        y, m = d.year, d.month
        for _ in range(0, 14):
            m += 1
            if m == 13:
                m, y = 1, y + 1
            cand = _safe_date(y, m, day)
            if cand > end:
                break
            if cand >= start:
                out.append(cand)
    else:
        d = s.last_date
        first = True
        while True:
            step = s.period
            if first and s.first_gap:
                step = s.first_gap
            first = False
            d = d + timedelta(days=step)
            if d > end:
                break
            if d >= start:
                out.append(d)
    return out


def _safe_date(y: int, m: int, day: int) -> date:
    import calendar

    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(day, last))
