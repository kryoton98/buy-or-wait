"""Financial-state reconstruction and the 90-day safety check.

The forecast is a deterministic day-level simulation:

* start from ``current_available_balance`` on the request date;
* add every projected cash flow in the 90-day window (recurring series,
  pending/scheduled debits, confirmed scheduled credits, facts extracted from
  messages/images);
* net all flows of the same day, then check the end-of-day balance against
  ``minimum_balance_to_keep``.

``amount_safe_to_pay`` is the largest payment on the request date that keeps
every end-of-day balance at or above the minimum (capped at the request), and
``earliest_full_payment_date`` is the first day on which a single full payment
passes the same check.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from .data import CASH_IGNORED_STATUSES, Dataset
from .recurrence import Series, detect_series, is_stale, next_dates

HORIZON_DAYS = 90
# every-N-day spending is projected slightly past the 90th day so that the last cycle that
# starts inside the window is still reserved (matches the reference decision style)
PERIODIC_HORIZON_DAYS = 92
# Monthly commitments are forecast for the request month and the next two calendar months
# (three billing cycles); every-N-day spending is projected through the 90-day horizon.
MONTHLY_CYCLES = 3


def monthly_horizon_end(R: date) -> date:
    import calendar

    y, m = R.year, R.month + MONTHLY_CYCLES - 1
    while m > 12:
        y, m = y + 1, m - 12
    return date(y, m, calendar.monthrange(y, m)[1])


@dataclass
class Flow:
    date: date
    amount: float  # signed: + credit, - debit
    label: str
    source: str  # 'series' | 'pending' | 'scheduled' | 'evidence'
    series_key: Optional[str] = None
    event_id: Optional[str] = None
    kind: str = "other"  # monthly | periodic | pending | scheduled | evidence | payment


@dataclass
class State:
    request_id: str
    user_id: str
    request_date: date
    horizon_end: date
    home: str
    balance: float
    min_balance: float
    series: list[Series]
    flows: list[Flow]
    one_offs: list[str]
    notes: list[str] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)
    protected: list[str] = field(default_factory=list)
    reducible_cats: list[str] = field(default_factory=list)
    stoppable_cats: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    max_installment_months: Optional[int] = None

    def series_by_key(self, key: str) -> Optional[Series]:
        for s in self.series:
            if s.key == key:
                return s
        return None


# --------------------------------------------------------------------------
# state construction
# --------------------------------------------------------------------------
def resolve_amounts(ds: Dataset, ev: pd.DataFrame, home: str, image_amounts: dict) -> pd.DataFrame:
    """Fill blank amounts from image evidence and convert to the home currency."""
    ev = ev.copy()
    ev["amount"] = ev.apply(lambda r: image_amounts.get(r.event_id, r.amount), axis=1)

    def conv(r):
        if pd.isna(r.amount):
            return float("nan")
        on = r.settlement_date or r.event_date
        return ds.convert(float(r.amount), r.currency, home, on)

    ev["amount_home"] = ev.apply(conv, axis=1)
    return ev


def build_state(ds: Dataset, req: pd.Series, facts: list[dict], image_amounts: dict) -> State:
    """Reconstruct the user's financial state for one request.

    ``facts`` are structured facts extracted from messages/images (see
    evidence.py). ``image_amounts`` maps event_id -> amount read from images.
    """
    uid = req.user_id
    prof = ds.profile(uid)
    home = prof.home_currency
    R = req.request_date
    H = R + timedelta(days=HORIZON_DAYS)
    ev = resolve_amounts(ds, ds.user_events(uid), home, image_amounts)

    notes: list[str] = []
    excluded: set[str] = set()
    for f in facts:
        if f.get("type") == "internal_transfer":
            for eid in f.get("event_ids", []):
                excluded.add(eid)
        if f.get("type") == "duplicate_event":
            excluded.update(f.get("event_ids", []))

    # duplicate representations: linked events of the same direction/amount/date
    hist = ev[(ev.status == "settled") & (ev.settlement_date < R)]
    series, one_offs = detect_series(hist, home, ds.convert, excluded)
    for s in series:
        if is_stale(s, R):
            s.stopped = True
            s.note = "inactive: no occurrence in the last cycle"
        if s.is_income and "final" in s.description.lower():
            s.stopped = True
            s.note = "final payroll received; no further salary"

    flows: list[Flow] = []
    # pending / scheduled debits are reserved on their settlement date
    fut = ev[ev.status.isin(["pending", "scheduled"]) & ~ev.event_id.isin(excluded)]
    for _, r in fut.iterrows():
        if r.direction == "debit":
            if pd.isna(r.amount_home):
                notes.append(f"{r.event_id} has no amount and no image evidence; skipped")
                continue
            d = max(r.settlement_date, R)
            if d <= H:
                flows.append(Flow(d, -float(r.amount_home), f"{r.status} {r.description}", r.status, event_id=r.event_id, kind=r.status))
        elif r.direction == "credit" and r.status == "scheduled" and r.category == "salary":
            # confirmed next salary: replaces the projected recurring salary on that date and
            # sets the level of the regular salary going forward
            flows.append(Flow(r.settlement_date, float(r.amount_home), f"scheduled {r.description}", "scheduled", event_id=r.event_id, kind="scheduled"))
            inc = [s for s in series if s.is_income and s.kind == "monthly" and not s.stopped]
            if inc:
                prim = max(inc, key=lambda s: s.amount)
                prim.amount = float(r.amount_home)
                prim.next_date_override = r.settlement_date
            else:
                series.append(_new_income_series(r.settlement_date, float(r.amount_home), None, home))

    st = State(
        request_id=req.request_id, user_id=uid, request_date=R, horizon_end=H, home=home,
        balance=float(prof.current_available_balance), min_balance=float(prof.minimum_balance_to_keep),
        series=series, flows=flows, one_offs=one_offs, notes=notes, facts=facts,
        protected=list(prof.expense_categories_to_protect_list),
        reducible_cats=list(prof.expense_categories_user_is_willing_to_reduce_list),
        stoppable_cats=list(prof.expense_categories_user_is_willing_to_stop_list),
        methods=list(prof.payment_methods_user_will_consider_list),
        max_installment_months=None if pd.isna(prof.max_installment_months) else int(prof.max_installment_months),
    )
    apply_facts(st, ds)
    project_series(st)
    return st


def apply_facts(st: State, ds: Dataset) -> None:
    """Apply structured facts (from messages/images) to the recurring series."""
    R, H = st.request_date, st.horizon_end
    income = [s for s in st.series if s.is_income]
    primary = None
    if income:
        # primary salary = the largest-amount monthly income series (or the largest overall)
        monthly = [s for s in income if s.kind == "monthly"]
        primary = max(monthly or income, key=lambda s: s.amount)

    for f in st.facts:
        t = f.get("type")
        if t == "income_stopped":
            for s in income:
                s.stopped = True
            st.notes.append("income stopped per employer message")
        elif t == "unconfirmed_income":
            # gig payouts / commissions / bonuses / prizes that are not confirmed: exclude those series
            which = f.get("scope", "variable")
            for s in income:
                if which == "all_variable" and (s.spread() > 1e-9 or s.kind == "periodic"):
                    s.stopped = True
                elif which == "commission" and s is not primary and s.spread() > 1e-9:
                    s.stopped = True
                elif which == "secondary" and s is not primary:
                    s.stopped = True
        elif t == "salary_amount":
            amt = ds.convert(float(f["amount"]), f.get("currency", st.home), st.home, f.get("date") or R)
            if primary is None:
                # create a new monthly income series from the confirmed date
                st.series.append(_new_income_series(f.get("date"), amt, st, st.home))
                primary = st.series[-1]
                income.append(primary)
                continue
            if f.get("date"):
                primary.next_date_override = f["date"]
            if f.get("scope") == "next_only":
                d = f.get("date") or _next_occurrence(primary, R)
                primary.amount_overrides[d] = amt
            else:
                primary.amount = amt
                primary.start_after = f.get("date")
            if f.get("exclude_other_income"):
                for s in income:
                    if s is not primary:
                        s.stopped = True
        elif t == "salary_date":
            if primary is not None:
                primary.next_date_override = f["date"]
                primary.day = f["date"].day
        elif t == "one_time_credit":
            amt = ds.convert(float(f["amount"]), f.get("currency", st.home), st.home, f["date"])
            if R <= f["date"] <= H:
                st.flows.append(Flow(f["date"], amt, f.get("label", "confirmed one-time credit"), "evidence"))
        elif t == "one_time_debit":
            amt = ds.convert(float(f["amount"]), f.get("currency", st.home), st.home, f["date"])
            if R <= f["date"] <= H:
                st.flows.append(Flow(f["date"], -amt, f.get("label", "confirmed one-time debit"), "evidence"))
        elif t == "rent_change":
            for s in st.series:
                if s.category in ("rent", "housing") and not s.is_income:
                    s.amount = s.amount * float(f.get("multiplier", 1.0))
                    s.note = "rent increased per lease renewal"
                    break
        elif t == "new_recurring_debit":
            if f.get("amount"):
                st.series.append(_new_debit_series(f, st))


def _next_occurrence(s: Series, R: date) -> date:
    ds = next_dates(s, R, R + timedelta(days=400))
    return ds[0] if ds else R


def _new_income_series(start: Optional[date], amt: float, st: Optional[State], home: str = None) -> Series:
    start = start or st.request_date
    home = home or st.home
    # last_date is placed one month before the start so that projection begins at ``start``
    y, m = start.year, start.month - 1
    if m == 0:
        y, m = y - 1, 12
    from .recurrence import _safe_date

    return Series(
        key="salary", category="salary", direction="credit", kind="monthly", day=start.day, period=None,
        last_date=_safe_date(y, m, start.day), amount=amt, amounts=[amt], dates=[], event_ids=[],
        flexibility="fixed", minimum_allowed_amount=None, description="confirmed salary", currency=home,
        event_type="income", is_income=True,
    )


def _new_debit_series(f: dict, st: State) -> Series:
    start = f.get("date") or st.request_date
    y, m = start.year, start.month - 1
    if m == 0:
        y, m = y - 1, 12
    from .recurrence import _safe_date

    return Series(
        key=f.get("category", "other"), category=f.get("category", "other"), direction="debit", kind="monthly",
        day=start.day, period=None, last_date=_safe_date(y, m, start.day), amount=float(f["amount"]),
        amounts=[float(f["amount"])], dates=[], event_ids=[], flexibility="fixed", minimum_allowed_amount=None,
        description=f.get("label", "new recurring expense"), currency=st.home, event_type="expense", is_income=False,
    )


def project_series(st: State) -> None:
    """Turn recurring series into dated flows within the window."""
    R, H = st.request_date, st.horizon_end
    scheduled_salary_dates = {f.date for f in st.flows if f.source == "scheduled" and f.amount > 0}
    HM = monthly_horizon_end(R)
    for s in st.series:
        if s.stopped:
            continue
        dates = next_dates(s, R, R + timedelta(days=PERIODIC_HORIZON_DAYS) if s.kind == "periodic" else min(H, HM))
        if s.next_date_override is not None:
            # the next occurrence moves to the override date; subsequent ones keep monthly spacing from it
            nd = s.next_date_override
            if s.kind == "monthly":
                from .recurrence import _safe_date

                dates = []
                d = nd
                while d <= min(H, HM):
                    if d >= R:
                        dates.append(d)
                    y, m = d.year, d.month + 1
                    if m == 13:
                        y, m = y + 1, 1
                    d = _safe_date(y, m, nd.day)
        for d in dates:
            if s.kind == "periodic" and d == R and s.next_date_override is None:
                continue  # every-N-day items due today are projected from tomorrow
            if s.start_after and d < s.start_after:
                continue
            if s.stop_after and d > s.stop_after:
                continue
            if s.is_income and d in scheduled_salary_dates:
                continue  # the scheduled 'Next confirmed salary' row already covers this date
            amt = s.amount_overrides.get(d, s.amount)
            st.flows.append(Flow(d, amt if s.is_income else -amt, s.description, "series", series_key=s.key, kind=s.kind))
    st.flows.sort(key=lambda f: f.date)


# --------------------------------------------------------------------------
# simulation
# --------------------------------------------------------------------------
# Within a day, cash moves in this order: dataset rows that already exist (pending /
# scheduled items), then every-N-day spending, then income, then monthly commitments,
# and finally any payment the user makes that day. The balance is checked after each
# stage, so the request-day payment is always judged against the end-of-day position.
STAGE = {"pending": 0, "scheduled": 0, "periodic": 1, "income": 2, "monthly": 3, "evidence": 3, "other": 3, "payment": 4}


def _day_groups(st: State, payments, changes):
    changes = changes or {}
    byday: dict[date, list] = defaultdict(list)
    byday[st.request_date] = []
    for f in st.flows:
        amt = f.amount
        if f.series_key in changes:
            new = changes[f.series_key]
            if new is None:
                continue
            amt = -abs(float(new))
        kind = "income" if amt > 0 and f.kind not in ("pending", "scheduled") else f.kind
        byday[f.date].append((amt, STAGE.get(kind, 3)))
    for d, a in payments or []:
        byday[d].append((-a, STAGE["payment"]))
    return byday


def simulate(st: State, payments: Optional[list[tuple[date, float]]] = None, changes: Optional[dict] = None, policy: str = None):
    """Return a list of (date, checkpoint_balance); several checkpoints per day are possible.

    ``payments`` are extra debits (the request's own payments) applied last on
    their day. ``changes`` maps series_key -> None (stop) or a new amount
    (reduce) applied to that series' future flows.
    """
    byday = _day_groups(st, payments, changes)
    bal = st.balance
    path = []
    for d in sorted(byday):
        items = byday[d]
        if not items:
            path.append((d, bal))
            continue
        stages = sorted(set(stage for _, stage in items))
        for stg in stages:
            bal += sum(a for a, s_ in items if s_ == stg)
            path.append((d, bal))
    return path


def min_balance_of(path) -> float:
    return min(b for _, b in path)


# half a cent of tolerance so that amounts rounded to two decimals verify cleanly
SAFETY_TOL = 0.005
# Forecast-uncertainty allowance used only when judging whether a *plan* (installments, a
# partial schedule or a full payment after spending changes) stays above the minimum: variable
# spending is estimated from noisy history, so a plan that misses the floor by less than this
# fraction of the reserved outflow is treated as feasible. The safe amount and the earliest
# date are always computed without it.
PLAN_TOL_FRAC = 0.012


def is_safe(st: State, payments, changes=None, tolerance: bool = False) -> bool:
    path = simulate(st, payments, changes)
    low = min_balance_of(path)
    if low >= st.min_balance - SAFETY_TOL:
        return True
    if not tolerance:
        return False
    drawdown = max(0.0, st.balance - min_balance_of(simulate(st, None, changes)))
    return low >= st.min_balance - SAFETY_TOL - PLAN_TOL_FRAC * drawdown


def safe_amount_today(st: State, requested: float, changes=None) -> float:
    """Largest payment on the request date that keeps the forecast above the minimum."""
    path = simulate(st, None, changes)
    room = min_balance_of(path) - st.min_balance
    return max(0.0, min(requested, room))


def earliest_full_payment(st: State, amount: float, changes=None) -> Optional[date]:
    """First date in the window on which a single payment of ``amount`` is safe."""
    path = simulate(st, None, changes)
    dates = [d for d, _ in path]
    bals = [b for _, b in path]
    n = len(path)
    suffix = [0.0] * n
    m = float("inf")
    for i in range(n - 1, -1, -1):
        m = min(m, bals[i])
        suffix[i] = m
    d = st.request_date
    while d <= st.horizon_end:
        # the payment is applied at the end of day d: it lowers every checkpoint from the end of d on
        i = 0
        while i < n and dates[i] <= d:
            i += 1
        # checkpoints strictly after d
        after = suffix[i] if i < n else float("inf")
        # end-of-day balance on d (last checkpoint with date <= d)
        eod = bals[i - 1] if i > 0 else st.balance
        low = min(eod, after)
        if low - amount >= st.min_balance - SAFETY_TOL:
            return d
        d += timedelta(days=1)
    return None


def path_min_after(st: State, payments, changes=None) -> float:
    return min_balance_of(simulate(st, payments, changes))
