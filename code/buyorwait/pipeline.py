"""End-to-end pipeline: evidence -> financial state -> forecast -> plan -> verified output row."""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from .data import Dataset, OUTPUT_COLUMNS, load_dataset
from .evidence import LLMExtractor, MessageReading, OCRCache, merge_llm_message, read_image_amount, read_message
from .forecast import build_state
from .planner import Decision, decide, fmt_safe, verify


class Agent:
    def __init__(self, dataset_root: str, code_root: Path, use_llm: str = "auto", verbose: bool = False):
        self.ds: Dataset = load_dataset(dataset_root)
        self.root = Path(dataset_root)
        self.code_root = code_root
        self.llm = LLMExtractor(code_root / "cache", code_root / "prompts")
        self.ocr = OCRCache(code_root / "cache" / "ocr_cache.json")
        # decisions consult the model only in "auto" mode; the key stays available to an explicit --llm-audit
        self.use_llm = use_llm != "off" and self.llm.enabled
        self.verbose = verbose
        self.readings: dict[str, MessageReading] = {}
        self.image_amounts: dict[str, float] = {}
        self.image_notes: dict[str, str] = {}
        self.verified = self._load_verified_images()
        self.problems: list[dict] = []

    # ------------------------------------------------------------------ evidence
    def _load_verified_images(self) -> dict:
        f = self.code_root / "cache" / "verified_image_amounts.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def read_all_evidence(self) -> None:
        home_by_user = self.ds.profiles.set_index("user_id").home_currency.to_dict()
        for _, row in self.ds.messages.iterrows():
            rd = read_message(row, home_by_user.get(row.user_id, ""))
            if rd.kind == "unknown" and self.use_llm:
                rd = merge_llm_message(rd, self.llm.read_message(str(row.message_text)))
            self.readings[row.message_id] = rd
        for _, row in self.ds.images.iterrows():
            eid = row.related_event_id
            path = self.root / "media" / "images" / f"{row.image_id}.png"
            amt, why = read_image_amount(path, self.ocr)
            if amt is None and self.use_llm:
                desc = self.ds.events.loc[self.ds.events.event_id == eid, "description"]
                out = self.llm.read_image(path, str(desc.iloc[0]) if len(desc) else "")
                if out and isinstance(out.get("amount"), (int, float)) and out["amount"] > 0:
                    amt, why = float(out["amount"]), f"vision model: {out.get('evidence', '')}"
            if amt is None and row.image_id in self.verified:
                amt, why = float(self.verified[row.image_id]["amount"]), "verified during development: " + self.verified[row.image_id].get("note", "")
            if amt is not None:
                self.image_amounts[eid] = amt
            self.image_notes[eid] = why

    def facts_for(self, user_id: str, request_id: str) -> tuple[list[dict], list[str]]:
        facts, notes = [], []
        msgs = self.ds.messages[(self.ds.messages.user_id == user_id)]
        for _, m in msgs.iterrows():
            rd = self.readings.get(m.message_id)
            if rd is None:
                continue
            facts.extend(rd.facts)
            if rd.summary:
                notes.append(f"{m.message_id}: {rd.summary}")
        return facts, notes

    # ------------------------------------------------------------------ decisions
    def describe_event(self, eid: str) -> str:
        row = self.ds.events[self.ds.events.event_id == eid]
        return str(row.iloc[0].description).lower() if len(row) else eid

    def decide_request(self, req: pd.Series) -> Decision:
        facts, notes = self.facts_for(req.user_id, req.request_id)
        st = build_state(self.ds, req, facts, self.image_amounts)
        st.notes.extend(notes)
        opts = self.ds.options[self.ds.options.request_id == req.request_id]
        dec = decide(st, req, opts, self.describe_event)
        probs = verify(dec, st, req, opts)
        if probs:
            self.problems.append({"request_id": req.request_id, "problems": probs})
            dec = self._fallback(dec, st, req, opts, probs)
        dec.debug["state"] = {
            "balance": st.balance, "min_balance": st.min_balance, "series": [s.describe() for s in st.series],
            "one_offs": st.one_offs, "notes": st.notes, "facts": [{k: (v.isoformat() if isinstance(v, date) else v) for k, v in f.items()} for f in facts],
        }
        return dec

    def _fallback(self, dec: Decision, st, req, opts, probs) -> Decision:
        """If verification fails, fall back to the most conservative consistent answer."""
        from .planner import fmt_money, fmt_date_long
        cur = st.home
        amount = float(req.requested_amount)
        dec.recommended_payment_method = "not_recommended"
        dec.affordability_status = "not_affordable"
        dec.payment_plan = "none"
        dec.spending_changes_needed = "none"
        dec.decision_explanation = (f"Do not make this payment by {fmt_date_long(req.desired_completion_date)}. "
                                    f"None of the available options could be verified to keep the {fmt_money(cur, st.min_balance)} minimum protected.")
        dec.debug["fallback"] = probs
        return dec

    @staticmethod
    def to_row(dec: Decision) -> dict:
        return {
            "request_id": dec.request_id,
            "amount_safe_to_pay": fmt_safe(dec.amount_safe_to_pay),
            "affordability_status": dec.affordability_status,
            "recommended_payment_method": dec.recommended_payment_method,
            "payment_plan": dec.payment_plan,
            "earliest_date_for_full_payment": dec.earliest_date_for_full_payment.isoformat() if dec.earliest_date_for_full_payment else "",
            "spending_changes_needed": dec.spending_changes_needed,
            "decision_explanation": dec.decision_explanation,
        }

    def run(self, requests: pd.DataFrame, debug_path: Optional[Path] = None) -> pd.DataFrame:
        rows, dbg = [], []
        t0 = time.time()
        for i, (_, req) in enumerate(requests.iterrows(), 1):
            dec = self.decide_request(req)
            rows.append(self.to_row(dec))
            dbg.append({"request_id": req.request_id, "user_id": req.user_id, **dec.debug})
            if self.verbose and i % 25 == 0:
                print(f"  {i}/{len(requests)} requests decided ({time.time() - t0:.1f}s)")
        if debug_path is not None:
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            with open(debug_path, "w") as f:
                for d in dbg:
                    f.write(json.dumps(d, default=str) + "\n")
        return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)

    def usage(self) -> dict:
        return self.llm.usage()
