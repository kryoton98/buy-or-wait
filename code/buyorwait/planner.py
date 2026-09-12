"""Plan generation, ranking and deterministic verification.

Candidate plans:

* ``full_payment`` today (optionally with spending changes);
* ``installments`` that exactly follow a supplied payment option;
* ``partial_payment``: ``amount_safe_to_pay`` today + the remainder on
  ``earliest_date_for_full_payment``;
* ``wait``: one full payment on ``earliest_date_for_full_payment``.

Eligibility follows the user's ``payment_methods_user_will_consider`` and
``max_installment_months``; every candidate is verified against the 90-day
simulation before it is ranked. Ranking follows the statement exactly:
complete by the deadline, no spending changes, lowest total paid, earliest
start, fewest payments, lowest payment_option_id.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from .forecast import State, earliest_full_payment, is_safe, safe_amount_today, simulate, min_balance_of
from .recurrence import Series


@dataclass
class Change:
    action: str  # 'stop' | 'reduce_to'
    series: Series
    event_id: str
    new_amount: Optional[float] = None

    def token(self) -> str:
        if self.action == "stop":
            return f"stop:{self.event_id}"
        return f"reduce_to:{self.event_id}:{fmt_amount(self.new_amount)}"

    def saved_per_occurrence(self) -> float:
        return self.series.amount if self.action == "stop" else self.series.amount - float(self.new_amount)

    def saved_per_month(self) -> float:
        per = 1.0 if self.series.kind == "monthly" else 30.0 / float(self.series.period or 30)
        return self.saved_per_occurrence() * per


@dataclass
class Plan:
    method: str  # full_payment | installments | partial_payment | wait | not_recommended
    payments: list[tuple[date, float]]
    changes: list[Change] = field(default_factory=list)
    option_id: Optional[str] = None
    total: float = 0.0
    note: str = ""

    @property
    def completes_on(self) -> Optional[date]:
        return max(d for d, _ in self.payments) if self.payments else None

    def starts_on(self) -> Optional[date]:
        return min(d for d, _ in self.payments) if self.payments else None


@dataclass
class Decision:
    request_id: str
    amount_safe_to_pay: float
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: Optional[date]
    spending_changes_needed: str
    decision_explanation: str
    plan: Optional[Plan] = None
    debug: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------
# formatting helpers
# ----------------------------------------------------------------------------
def fmt_amount(x: float) -> str:
    """Plan-style amount: integers without decimals, otherwise two decimals (620.40)."""
    x = round(float(x) + 0.0, 2)
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.2f}"


def fmt_safe(x: float) -> str:
    """amount_safe_to_pay style: shortest exact decimal (603.3, 17229139.2, 873000)."""
    x = round(float(x) + 0.0, 2)
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    s = f"{x:.2f}".rstrip("0")
    return s


def fmt_money(cur: str, x: float) -> str:
    x = round(float(x), 2)
    if abs(x - round(x)) < 1e-9:
        return f"{cur} {int(round(x)):,}"
    return f"{cur} {x:,.2f}"


def fmt_date_long(d: date) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"


# ----------------------------------------------------------------------------
# spending changes
# ----------------------------------------------------------------------------
def eligible_changes(st: State) -> list[Change]:
    """Flexible recurring expenses the user permits to stop or reduce."""
    out = []
    for s in st.series:
        if s.is_income or s.stopped or not s.event_ids:
            continue
        if s.category in st.protected:
            continue
        flex = s.flexibility
        eid = s.event_ids[-1]
        if flex in ("stoppable", "reducible_or_stoppable") and s.category in st.stoppable_cats:
            out.append(Change("stop", s, eid))
        if flex in ("reducible", "reducible_or_stoppable") and s.category in st.reducible_cats and s.minimum_allowed_amount:
            if s.minimum_allowed_amount < s.amount - 1e-9:
                out.append(Change("reduce_to", s, eid, float(s.minimum_allowed_amount)))
    return out


def _changes_map(changes: list[Change]) -> dict:
    m = {}
    for c in changes:
        m[c.series.key] = None if c.action == "stop" else float(c.new_amount)
    return m


def find_change_set(st: State, payments: list[tuple[date, float]], candidates: list[Change], max_changes: int = 3) -> Optional[list[Change]]:
    """Smallest set of permitted changes (stop and reduce on different events) making ``payments`` safe."""
    if not candidates:
        return None
    found = []
    for k in range(1, max_changes + 1):
        for combo in itertools.combinations(candidates, k):
            keys = [c.series.key for c in combo]
            if len(set(keys)) != len(keys):
                continue  # stop and reduce on the same event are mutually exclusive
            if is_safe(st, payments, _changes_map(list(combo)), tolerance=True):
                found.append(list(combo))
    if not found:
        return None
    # the lightest intervention wins: least monthly spending removed, then fewer changes
    found.sort(key=lambda cs: (round(sum(c.saved_per_month() for c in cs), 2), len(cs), [c.action != "stop" for c in cs]))
    best = found[0]
    best.sort(key=lambda c: (c.action != "stop", int(c.event_id.split("_")[-1])))
    return best


# ----------------------------------------------------------------------------
# candidate plans
# ----------------------------------------------------------------------------
def installment_schedule(opt: pd.Series) -> list[tuple[date, float]]:
    n = int(opt.number_of_payments)
    freq = int(opt.payment_frequency_days) if not pd.isna(opt.payment_frequency_days) else 30
    first = opt.first_payment_date
    amt = float(opt.payment_amount)
    return [(first + timedelta(days=freq * k), amt) for k in range(n)]


def build_candidates(st: State, req: pd.Series, options: pd.DataFrame, safe_today: float, earliest: Optional[date]) -> tuple[list[Plan], list[str]]:
    R = st.request_date
    amount = float(req.requested_amount)
    deadline = req.desired_completion_date
    methods = st.methods
    notes = []
    plans: list[Plan] = []
    cands = eligible_changes(st)

    # full payment today
    if "full_payment" in methods:
        pay = [(R, amount)]
        if is_safe(st, pay):
            plans.append(Plan("full_payment", pay, [], None, amount))
        else:
            cs = find_change_set(st, pay, cands)
            if cs:
                plans.append(Plan("full_payment", pay, cs, None, amount, note="with spending changes"))

    # wait: full payment on the earliest safe date (capacity without changes)
    if "full_payment" in methods and earliest is not None and earliest > R:
        plans.append(Plan("wait", [(earliest, amount)], [], None, amount))

    # partial payment: safe amount today, remainder on the earliest full-payment date
    if "partial_payment" in methods and bool(req.allows_partial_payment) and earliest is not None and 0 < safe_today < amount - 1e-9:
        rest = round(amount - safe_today, 2)
        pay = [(R, safe_today), (earliest, rest)]
        if earliest <= deadline and earliest > R and is_safe(st, pay, tolerance=True):
            plans.append(Plan("partial_payment", pay, [], None, amount))
        elif earliest <= deadline and earliest > R:
            notes.append("partial payment schedule failed verification")

    # installments that exactly follow a supplied option
    if "installments" in methods and st.max_installment_months is not None:
        for _, opt in options.iterrows():
            if str(opt.payment_method) != "installments":
                continue
            n = int(opt.number_of_payments)
            if n > st.max_installment_months:
                continue
            sched = installment_schedule(opt)
            if any(d < R for d, _ in sched):
                continue
            total = float(opt.total_payable_amount)
            if is_safe(st, sched, tolerance=True):
                plans.append(Plan("installments", sched, [], str(opt.payment_option_id), total))
            else:
                cs = find_change_set(st, sched, cands)
                if cs:
                    plans.append(Plan("installments", sched, cs, str(opt.payment_option_id), total, note="with spending changes"))
    return plans, notes


def rank_key(p: Plan, deadline: date):
    late = 0 if (p.completes_on is not None and p.completes_on <= deadline) else 1
    opt_num = int(p.option_id.split("_")[-1]) if p.option_id else 0
    return (late, 1 if p.changes else 0, round(p.total, 2), p.starts_on(), len(p.payments), opt_num)


# ----------------------------------------------------------------------------
# decision
# ----------------------------------------------------------------------------
def decide(st: State, req: pd.Series, options: pd.DataFrame, describe_event) -> Decision:
    R = st.request_date
    amount = float(req.requested_amount)
    deadline = req.desired_completion_date
    cur = st.home

    safe_today = round(safe_amount_today(st, amount), 2)
    earliest = earliest_full_payment(st, amount)
    plans, notes = build_candidates(st, req, options, safe_today, earliest)
    plans.sort(key=lambda p: rank_key(p, deadline))
    chosen = plans[0] if plans else None

    min_s = fmt_money(cur, st.min_balance)
    if chosen is None:
        method = "not_recommended"
        status = "not_affordable"
        plan_s = "none"
        changes_s = "none"
        partial_only = set(st.methods) <= {"partial_payment"}
        if safe_today >= amount - 0.005 and "full_payment" not in st.methods:
            # the money is there, but no acceptable method exists (no full payment, no fitting option)
            reasons = []
            if "installments" in st.methods:
                reasons.append("no supplied installment option fits the user's installment limit")
            if "partial_payment" in st.methods and not bool(req.allows_partial_payment):
                reasons.append("the request does not allow a partial payment")
            expl = (f"No plan is recommended for the {fmt_money(cur, amount)} request. The full amount could be covered today while keeping the "
                    f"{min_s} minimum, but a single full payment is not a method the user will consider"
                    + (" and " + " and ".join(reasons) if reasons else "") + ".")
        elif partial_only and bool(req.allows_partial_payment) and safe_today > 0:
            expl = (f"Do not proceed with the {fmt_money(cur, amount)} request. Although {fmt_money(cur, safe_today)} is available today, "
                    f"the full amount cannot be completed safely within 90 days.")
        else:
            expl = f"Do not make this payment by {fmt_date_long(deadline)}. None of the available options keeps the {min_s} minimum protected."
    else:
        method = chosen.method
        plan_s = "|".join(f"{d.isoformat()}:{fmt_amount(a)}" for d, a in chosen.payments)
        changes_s = "|".join(c.token() for c in chosen.changes) if chosen.changes else "none"
        if method == "full_payment" and not chosen.changes:
            status = "affordable_now"
            expl = f"Pay {fmt_money(cur, amount)} today. This leaves at least {min_s} available over the next 90 days."
        elif method == "full_payment":
            status = "affordable_with_plan"
            expl = f"{_changes_phrase(chosen.changes, cur, describe_event)}, then pay {fmt_money(cur, amount)} today. This leaves at least {min_s} available."
        elif method == "wait":
            status = "affordable_later"
            d = chosen.payments[0][0]
            if d <= deadline:
                expl = f"Pay {fmt_money(cur, amount)} in full on {fmt_date_long(d)}. Paying earlier would take the balance below the {min_s} minimum."
            else:
                expl = (f"Wait until {fmt_date_long(d)}, then pay {fmt_money(cur, amount)} in full. Paying sooner would put the {min_s} minimum at risk, "
                        f"and no option completes the request by {fmt_date_long(deadline)}.")
        elif method == "partial_payment":
            status = "affordable_with_plan"
            (d1, a1), (d2, a2) = chosen.payments
            expl = (f"Pay {fmt_money(cur, a1)} today and the remaining {fmt_money(cur, a2)} on {fmt_date_long(d2)}. "
                    f"This completes the full request and keeps the {min_s} minimum protected.")
        else:  # installments
            status = "affordable_with_plan"
            n = len(chosen.payments)
            a = chosen.payments[0][1]
            prefix = f"{_changes_phrase(chosen.changes, cur, describe_event)}, then use" if chosen.changes else "Use"
            expl = f"{prefix} {n} installments of {fmt_money(cur, a)}, starting {fmt_date_long(chosen.payments[0][0])}. This leaves at least {min_s} available."

    # invariants required by the statement
    if status == "affordable_now":
        earliest_out = R
    else:
        earliest_out = earliest
    dec = Decision(
        request_id=req.request_id,
        amount_safe_to_pay=safe_today,
        affordability_status=status,
        recommended_payment_method=method,
        payment_plan=plan_s,
        earliest_date_for_full_payment=earliest_out,
        spending_changes_needed=changes_s,
        decision_explanation=expl,
        plan=chosen,
        debug={"candidates": [(p.method, p.option_id, round(p.total, 2), [c.token() for c in p.changes], p.completes_on) for p in plans], "notes": notes + st.notes},
    )
    return dec


def _changes_phrase(changes: list[Change], cur: str, describe_event) -> str:
    parts = []
    for c in changes:
        name = describe_event(c.event_id)
        if c.action == "stop":
            parts.append(f"stop the {name}")
        else:
            parts.append(f"reduce the {name} to {fmt_money(cur, c.new_amount)}")
    s = " and ".join(parts)
    return s[0].upper() + s[1:]


def verify(dec: Decision, st: State, req: pd.Series, options: pd.DataFrame) -> list[str]:
    """Deterministic post-checks; returns a list of violated invariants (empty when valid)."""
    problems = []
    amount = float(req.requested_amount)
    if not (0 <= dec.amount_safe_to_pay <= amount + 1e-6):
        problems.append("amount_safe_to_pay out of range")
    if dec.affordability_status == "affordable_now" and dec.earliest_date_for_full_payment != st.request_date:
        problems.append("affordable_now requires earliest == request_date")
    if dec.recommended_payment_method == "partial_payment":
        if dec.affordability_status != "affordable_with_plan":
            problems.append("partial requires affordable_with_plan")
        parts = dec.payment_plan.split("|")
        if len(parts) != 2:
            problems.append("partial plan must have two payments")
        else:
            tot = sum(float(p.split(":")[1]) for p in parts)
            if abs(tot - amount) > 0.011:
                problems.append("partial payments must add up to the request")
    if dec.recommended_payment_method == "installments":
        opt = options[options.payment_option_id == (dec.plan.option_id if dec.plan else "")]
        if opt.empty:
            problems.append("installment plan must match a supplied option")
        else:
            sched = installment_schedule(opt.iloc[0])
            exp = "|".join(f"{d.isoformat()}:{fmt_amount(a)}" for d, a in sched)
            if exp != dec.payment_plan:
                problems.append("installment schedule mismatch")
    if dec.spending_changes_needed != "none":
        toks = dec.spending_changes_needed.split("|")
        if len(toks) > 3:
            problems.append("more than three spending changes")
        seen = set()
        for t in toks:
            eid = t.split(":")[1]
            if eid in seen:
                problems.append("stop and reduce on the same event")
            seen.add(eid)
    if dec.plan is not None and dec.plan.payments:
        if not is_safe(st, dec.plan.payments, _changes_map(dec.plan.changes), tolerance=True):
            problems.append("chosen plan fails the 90-day safety check")
    return problems
