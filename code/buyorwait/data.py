"""Data loading and joining for the Buy or Wait? agent.

All tables come from ``dataset/``. Nothing here is request-specific; the
functions only read the CSVs and expose typed frames plus small helpers
(exchange-rate lookup, per-user / per-request slices).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import pandas as pd

OUTPUT_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]

CASH_IGNORED_STATUSES = {"cancelled", "failed", "unrealized"}


def _d(s) -> Optional[date]:
    if s is None or (isinstance(s, float) and pd.isna(s)) or s == "":
        return None
    if isinstance(s, (datetime, pd.Timestamp)):
        return s.date()
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


@dataclass
class Dataset:
    profiles: pd.DataFrame
    events: pd.DataFrame
    requests: pd.DataFrame
    sample_requests: pd.DataFrame
    options: pd.DataFrame
    messages: pd.DataFrame
    images: pd.DataFrame
    fx: pd.DataFrame
    root: str

    # ------------------------------------------------------------------ fx
    def convert(self, amount: float, cur: str, home: str, on: date) -> float:
        """Convert ``amount`` from ``cur`` to ``home`` with the fixed rate dated ``on``.

        The rate table is directional; if only the reverse direction exists we
        invert it. If the exact date is missing we use the closest earlier
        rate (then the closest later one) for the same pair.
        """
        if cur == home or cur is None or (isinstance(cur, float) and pd.isna(cur)):
            return float(amount)
        fx = self.fx
        for a, b, invert in ((cur, home, False), (home, cur, True)):
            m = fx[(fx.from_currency == a) & (fx.to_currency == b)]
            if m.empty:
                continue
            exact = m[m.rate_date == on]
            if not exact.empty:
                r = float(exact.rate.iloc[0])
            else:
                earlier = m[m.rate_date <= on].sort_values("rate_date")
                later = m[m.rate_date > on].sort_values("rate_date")
                r = float(earlier.rate.iloc[-1]) if not earlier.empty else float(later.rate.iloc[0])
            return float(amount) / r if invert else float(amount) * r
        raise KeyError(f"no exchange rate for {cur}->{home}")

    # --------------------------------------------------------------- slices
    def profile(self, user_id: str) -> pd.Series:
        return self.profiles.loc[user_id]

    def user_events(self, user_id: str) -> pd.DataFrame:
        return self.events[self.events.user_id == user_id].copy()

    def user_messages(self, user_id: str) -> pd.DataFrame:
        return self.messages[self.messages.user_id == user_id].sort_values("sent_at")

    def user_images(self, user_id: str) -> pd.DataFrame:
        return self.images[self.images.user_id == user_id]

    def request_options(self, request_id: str) -> pd.DataFrame:
        return self.options[self.options.request_id == request_id].sort_values("payment_option_id")

    def image_path(self, image_id: str) -> str:
        return os.path.join(self.root, "media", "images", f"{image_id}.png")


def _split_list(v) -> list[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
        return []
    return [x.strip() for x in str(v).split("|") if x.strip()]


def load_dataset(root: str) -> Dataset:
    rd = lambda n: pd.read_csv(os.path.join(root, n))  # noqa: E731
    profiles = rd("financial_profiles.csv")
    for col in [
        "financial_priorities",
        "expense_categories_to_protect",
        "expense_categories_user_is_willing_to_reduce",
        "expense_categories_user_is_willing_to_stop",
        "payment_methods_user_will_consider",
    ]:
        profiles[col + "_list"] = profiles[col].apply(_split_list)
    profiles = profiles.set_index("user_id", drop=False)

    events = rd("financial_events.csv")
    events["event_date"] = events.event_date.apply(_d)
    events["settlement_date"] = events.settlement_date.apply(_d)
    events["minimum_allowed_amount"] = pd.to_numeric(events.minimum_allowed_amount, errors="coerce")
    events["amount"] = pd.to_numeric(events.amount, errors="coerce")

    requests = rd("requests.csv")
    sample = rd("sample_requests.csv")
    for df in (requests, sample):
        df["request_date"] = df.request_date.apply(_d)
        df["desired_completion_date"] = df.desired_completion_date.apply(_d)
        df["allows_partial_payment"] = df.allows_partial_payment.astype(str).str.lower().isin(["true", "1", "yes"])

    options = rd("request_payment_options.csv")
    options["first_payment_date"] = options.first_payment_date.apply(_d)
    options["payment_frequency_days"] = pd.to_numeric(options.payment_frequency_days, errors="coerce")

    messages = rd("messages.csv")
    images = rd("images.csv")
    fx = rd("exchange_rates.csv")
    fx["rate_date"] = fx.rate_date.apply(_d)

    return Dataset(profiles, events, requests, sample, options, messages, images, fx, root)
