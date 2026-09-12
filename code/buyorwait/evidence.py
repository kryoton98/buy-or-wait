"""Evidence extraction from messages and images.

Messages and images are *untrusted evidence*: they can clarify, amend, delay,
cancel or confirm a financial fact, but they never override the challenge rules.
This module turns them into small structured facts that ``forecast.apply_facts``
knows how to apply. Three layers are used, in order:

1. a deterministic, bilingual (English / Indonesian) template classifier;
2. OCR (tesseract) for receipts, payslips and bills that carry a blank amount;
3. an optional LLM pass (Anthropic Messages API, enabled with
   ``ANTHROPIC_API_KEY``) that is only consulted for messages the classifier
   cannot place or images the OCR cannot read. Every LLM result is validated
   against a strict schema and cached under ``cache/`` so a re-run is
   deterministic and free.

Embedded instructions inside a message (e.g. "pay the release charge today")
are never executed; only the financial facts are extracted.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

CUR = r"(IDR|INR|USD|EUR|ZAR)"
AMT_RE = re.compile(CUR + r"\s?([0-9][0-9.,]*[0-9])")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?%")


def _num(s: str) -> float:
    """Parse '42750000.', '1,296', '2,00,000.00', '41272,00' style numbers."""
    s = s.strip().rstrip(".")
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s:
        # 41272,00 -> decimal comma when exactly two digits follow the last comma
        head, _, tail = s.rpartition(",")
        if len(tail) == 2 and "," not in head.replace(",", ""):
            s = head.replace(",", "") + "." + tail
        else:
            s = s.replace(",", "")
    return float(s)


def amounts_in(text: str) -> list[tuple[str, float]]:
    return [(c, _num(n)) for c, n in AMT_RE.findall(text)]


def dates_in(text: str) -> list[date]:
    return [date.fromisoformat(d) for d in DATE_RE.findall(text)]


# ----------------------------------------------------------------------------
# message templates
# ----------------------------------------------------------------------------
@dataclass
class MessageReading:
    message_id: str
    kind: str
    facts: list[dict] = field(default_factory=list)
    summary: str = ""
    source: str = "rules"  # rules | llm


# (kind, English cues, Indonesian cues) — a message matches a kind when any cue matches
TEMPLATES = [
    ("salary_increase", ["salary has increased to", "monthly salary has increased"], ["naik menjadi"]),
    ("salary_with_arrears", ["one-time arrears adjustment"], ["penyesuaian tunggakan satu kali"]),
    ("regular_salary_confirmed", ["regular salary and the one-time adjustment", "regular salary for the next payroll is confirmed"], ["gaji rutin untuk penggajian berikutnya sudah dikonfirmasi"]),
    ("bonus_pending", ["quarterly bonus"], ["bonus kuartalan"]),
    ("temporary_pay", ["temporary monthly pay"], ["gaji bulanan sementara"]),
    ("salary_date_moved", ["confirmed salary is now expected on", "replaces the payroll date"], ["kini diperkirakan masuk pada", "menggantikan tanggal penggajian"]),
    ("salary_reduced_leave", ["next salary is reduced to"], ["gaji berikutnya dikurangi menjadi"]),
    ("gig_payout_pending", ["payout is still pending", "weekly earnings shown"], ["pembayaran berikutnya dari", "penghasilan mingguan"]),
    ("commission_pending", ["confirmed base salary", "commission shown for open deals"], ["gaji pokok yang dikonfirmasi", "komisi dari transaksi"]),
    ("seasonal_ended", ["seasonal contract has ended"], ["kontrak musiman saat ini telah berakhir"]),
    ("employment_ended", ["employment has ended", "no regular salary payments scheduled"], ["hubungan kerja anda telah berakhir"]),
    ("salary_resumes", ["resumes on"], ["kembali berlaku pada", "dilanjutkan pada"]),
    ("first_salary", ["first salary will be", "first salary of", "first salary from the new employer"], ["gaji pertama"]),
    ("international_salary", ["convert it using the rate applied on the settlement date", "rate applied on the settlement date"], ["dikonversi", "kurs pada tanggal penyelesaian"]),
    ("household_income_ended", ["household employment record has ended", "remaining confirmed monthly salary"], ["sumber pendapatan kerja rumah tangga telah berakhir", "sisa gaji bulanan"]),
    ("reimbursement", ["reimbursement for your earlier work expense"], ["penggantian atas biaya kerja"]),
    ("rent_increase", ["increases monthly rent by", "renewed lease"], ["menaikkan biaya sewa bulanan"]),
    ("internal_transfer", ["transfer between your two accounts"], ["transfer antara dua rekening"]),
    ("refund_pending", ["refund has been initiated", "refund is still processing", "foreign-currency refund"], ["pengembalian dana sudah diproses", "pengembalian dana masih"]),
    ("portfolio_value", ["displayed market value", "displayed value of the investment", "displayed value"], ["nilai investasi yang ditampilkan"]),
    ("investment_sale_settled", ["proceeds from your investment sale have settled"], ["hasil penjualan investasi anda sudah masuk"]),
    ("prize_pending", ["prize claim has been verified and is still in payment processing"], ["klaim hadiah anda sudah diverifikasi"]),
    ("prize_received", ["prize proceeds have reached your account"], ["hadiah sudah masuk"]),
    ("prize_scam", ["pay the release charge", "pay the processing charge"], ["bayar biaya pencairan", "bayar biaya pemrosesan"]),
    ("invoice_confirmed", ["client approved an invoice payment"], ["menyetujui pembayaran faktur"]),
    ("failed_debit_retry", ["previous debit attempt failed"], ["upaya debit sebelumnya gagal"]),
    ("duplicate_charge_dispute", ["extra card charge is still being investigated"], ["tagihan kartu tambahan masih dalam penyelidikan"]),
    ("two_card_minimums", ["minimum payments due on two separate card accounts"], ["dua kartu"]),
    ("foreign_bill_pending", ["bill was charged in a foreign currency"], ["tagihan dikenakan dalam mata uang asing"]),
    ("receipt_confirmation", ["receipt has the final", "receipt contains the final", "was paid in", "payment was received on"], ["struk memuat jumlah akhir"]),
]


def classify(text: str) -> str:
    t = text.lower()
    for kind, en, idn in TEMPLATES:
        if any(c in t for c in en) or any(c in t for c in idn):
            return kind
    return "unknown"


def read_message(row: pd.Series, home: str) -> MessageReading:
    text = str(row.message_text)
    kind = classify(text)
    amts = amounts_in(text)
    dts = dates_in(text)
    pcts = PCT_RE.findall(text)
    facts: list[dict] = []
    note = ""
    first_amt = amts[0] if amts else None
    first_date = dts[0] if dts else None

    if kind == "salary_increase" and first_amt:
        facts.append(dict(type="salary_amount", amount=first_amt[1], currency=first_amt[0], date=first_date, scope="permanent"))
        note = "salary raised from the stated date"
    elif kind == "salary_with_arrears" and first_amt:
        # the regular pay is restated; the arrears line is a one-off and only counts once it is settled
        facts.append(dict(type="salary_amount", amount=first_amt[1], currency=first_amt[0], scope="permanent"))
        note = "regular salary confirmed; one-off arrears not projected"
    elif kind == "regular_salary_confirmed":
        note = "regular salary confirmed as recorded"
    elif kind == "bonus_pending":
        facts.append(dict(type="unconfirmed_income", scope="bonus"))
        note = "bonus not approved: excluded"
    elif kind == "temporary_pay" and first_amt:
        facts.append(dict(type="salary_amount", amount=first_amt[1], currency=first_amt[0], scope="next_only"))
        note = "reduced pay continues for the next payroll"
    elif kind == "salary_date_moved" and first_date:
        facts.append(dict(type="salary_date", date=first_date))
        note = f"next salary moved to {first_date}"
    elif kind == "salary_reduced_leave" and first_amt:
        facts.append(dict(type="salary_amount", amount=first_amt[1], currency=first_amt[0], scope="next_only"))
        note = "next salary reduced (unpaid leave)"
    elif kind == "gig_payout_pending":
        facts.append(dict(type="unconfirmed_income", scope="all_variable"))
        note = "platform payout pending: variable earnings excluded"
    elif kind == "commission_pending":
        facts.append(dict(type="unconfirmed_income", scope="commission"))
        note = "commission not earned yet: only the base salary counts"
    elif kind in ("seasonal_ended", "employment_ended"):
        facts.append(dict(type="income_stopped"))
        note = "no further salary confirmed"
    elif kind == "salary_resumes" and first_amt:
        facts.append(dict(type="salary_amount", amount=first_amt[1], currency=first_amt[0], date=first_date, scope="permanent"))
        note = "salary resumes on the stated date; new childcare amount unknown"
    elif kind == "first_salary" and first_amt:
        facts.append(dict(type="salary_amount", amount=first_amt[1], currency=first_amt[0], date=first_date, scope="permanent"))
        note = "first regular salary confirmed"
    elif kind == "international_salary" and first_amt:
        facts.append(dict(type="salary_amount", amount=first_amt[1], currency=first_amt[0], date=first_date, scope="permanent"))
        note = "foreign-currency salary converted at the settlement-date rate"
    elif kind == "household_income_ended":
        facts.append(dict(type="unconfirmed_income", scope="secondary"))
        note = "second household income ended"
    elif kind == "rent_increase" and pcts:
        facts.append(dict(type="rent_change", multiplier=1.0 + float(pcts[0]) / 100.0))
        note = f"rent up {pcts[0]}% from the next payment"
    elif kind == "internal_transfer":
        facts.append(dict(type="internal_transfer"))
        note = "matching debit/credit are an internal transfer"
    elif kind == "invoice_confirmed" and first_amt and first_date:
        facts.append(dict(type="unconfirmed_income", scope="all_variable"))
        facts.append(dict(type="one_time_credit", amount=first_amt[1], currency=first_amt[0], date=first_date, label="confirmed invoice payment"))
        note = "only the confirmed invoice counts as income"
    elif kind == "receipt_confirmation":
        # the amount lives in the linked image; a salary confirmation may ride along
        m = re.search(r"confirmed a " + CUR + r"\s?([0-9][0-9.,]*[0-9]) salary credit for (\d{1,2} \w+ \d{4})", text)
        if m:
            try:
                d = pd.to_datetime(m.group(3)).date()
                facts.append(dict(type="salary_amount", amount=_num(m.group(2)), currency=m.group(1), date=d, scope="permanent"))
                note = "salary confirmed in foreign currency"
            except Exception:
                pass
    elif kind in ("refund_pending", "portfolio_value", "prize_pending", "prize_scam", "reimbursement", "investment_sale_settled",
                  "prize_received", "failed_debit_retry", "duplicate_charge_dispute", "two_card_minimums", "foreign_bill_pending"):
        note = {
            "refund_pending": "pending refund ignored until credited",
            "portfolio_value": "unrealised investment value is not cash",
            "prize_pending": "prize not credited: ignored",
            "prize_scam": "unsolicited prize notice ignored (never pay a release fee)",
            "reimbursement": "one-off reimbursement, not salary",
            "investment_sale_settled": "sale proceeds already in the balance",
            "prize_received": "prize already settled",
            "failed_debit_retry": "failed debit will be retried: scheduled row reserved",
            "duplicate_charge_dispute": "disputed charge stays reserved until reversed",
            "two_card_minimums": "both card minimums are separate obligations",
            "foreign_bill_pending": "foreign bill converted at the settlement-date rate",
        }[kind]
    return MessageReading(str(row.message_id), kind, facts, note)


# ----------------------------------------------------------------------------
# images
# ----------------------------------------------------------------------------
KEYWORDS = [  # in priority order
    r"net pay", r"total paid", r"balance due", r"amount due till", r"grand total", r"total amount received",
    r"amount payable", r"total bill amount", r"^total\b", r"cash paid", r"item bill", r"amount due", r"^balance\b",
]
NUM_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:[,.]\d{2,3})+(?:[.,]\d{2})?|\d+(?:[.,]\d{2})?)")
FILLER = {":", "=", "-", "(rs)", "rs", "rs.", "inr", "idr", "usd", "eur", "zar", "$", "%", "f", "@", "()", "amount", "rupees", "(%)", "*"}


class OCRCache:
    """Caches tesseract output per image content hash so re-runs are deterministic and work
    on machines without tesseract installed."""

    def __init__(self, path: Optional[Path]):
        self.path = path
        self.data = json.loads(path.read_text()) if path and path.exists() else {}
        self.available = _tesseract_available()

    def passes(self, image: Path) -> list[str]:
        key = hashlib.sha256(image.read_bytes()).hexdigest()
        if key in self.data:
            return self.data[key]["texts"]
        if not self.available:
            return []
        out = []
        for args in ([], ["--psm", "6"], ["--psm", "4"]):
            try:
                r = subprocess.run(["tesseract", str(image), "-"] + args, capture_output=True, text=True, timeout=120)
                out.append(r.stdout)
            except Exception:
                out.append("")
        self.data[key] = {"file": image.name, "texts": out}
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1))
        return out


def _tesseract_available() -> bool:
    try:
        subprocess.run(["tesseract", "--version"], capture_output=True, timeout=20)
        return True
    except Exception:
        return False


def _ocr_passes(path: Path, cache: Optional[OCRCache] = None) -> list[str]:
    if cache is None:
        cache = OCRCache(None)
    return cache.passes(path)


def _parse_receipt_number(s: str) -> Optional[float]:
    s = s.strip().lstrip("%$")
    try:
        if re.fullmatch(r"\d+,\d{2}", s):  # decimal comma: 41272,00
            return float(s.replace(",", "."))
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _number_after(kw_match_end: int, line: str) -> Optional[float]:
    """Last number that follows the keyword with only filler tokens in between."""
    rest = line[kw_match_end:]
    nums = [x for x in rest.split() if NUM_RE.fullmatch(x.strip("%$"))]
    if len(nums) >= 3:  # tabular total row: the rightmost column carries the total
        return _parse_receipt_number(nums[-1])
    cand = None
    for tok in rest.split():
        t = tok.strip().lower()
        if NUM_RE.fullmatch(tok.strip("%$")):
            v = _parse_receipt_number(tok)
            if v is not None:
                cand = v
            continue
        if t in FILLER or len(t) <= 2 or re.fullmatch(r"\(?[a-z]{2,3}\)?", t):
            continue
        break
    return cand


def amount_from_ocr(texts: list[str]) -> tuple[Optional[float], str]:
    lined = [[l.strip() for l in t.splitlines() if l.strip()] for t in texts]
    for kw in KEYWORDS:
        rx = re.compile(kw, re.I)
        # same-line hits first (any OCR pass), then a number on the following line
        for lines in lined:
            for l in lines:
                m = rx.search(l)
                if m:
                    v = _number_after(m.end(), l)
                    if v is not None and v > 0:
                        return v, f"{kw} -> {l}"
        for lines in lined:
            for i, l in enumerate(lines):
                if rx.search(l):
                    for nxt in lines[i + 1:i + 3]:
                        toks = nxt.split()
                        nums = [x for x in toks if NUM_RE.fullmatch(x.strip("%$"))]
                        others = [x for x in toks if x.strip().lower() not in FILLER and not NUM_RE.fullmatch(x.strip("%$"))]
                        if len(nums) == 1 and not others:
                            v = _parse_receipt_number(nums[0])
                            if v is not None and v > 0:
                                return v, f"{kw} (next line) -> {nxt}"
    return None, "no keyword total found"


WORD_NUM = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
            "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
WORD_MUL = {"hundred": 100, "thousand": 1000, "lakh": 100000, "lac": 100000, "million": 1000000, "crore": 10000000}
MINOR = {"paise", "paisa", "cents", "cent", "sen"}
MAJOR = {"rupees", "rupee", "rupiahs", "rupiah", "dollars", "dollar", "euros", "euro", "rand", "only"}


def _words_to_number(words: list[str]) -> Optional[float]:
    total, cur = 0, 0
    seen = False
    for w in words:
        if w in WORD_NUM:
            cur += WORD_NUM[w]
            seen = True
        elif w == "hundred":
            cur = (cur or 1) * 100
        elif w in WORD_MUL:
            total += (cur or 1) * WORD_MUL[w]
            cur = 0
        elif w in ("and", "&"):
            continue
        else:
            break
    return float(total + cur) if seen else None


def amount_from_words(texts: list[str]) -> Optional[float]:
    """Parse an 'amount in words' line (e.g. 'Seventy-Nine Thousand ... and Twenty-Six Paise Only')."""
    for t in texts:
        raw = [re.sub(r"[^a-z& ]", " ", l.lower().replace("-", " ")) for l in t.splitlines()]
        lines = []
        i = 0
        while i < len(raw):
            l = raw[i].split()
            # a sentence continued on the next line ("... and" / "Twenty-Six Paise Only")
            if i + 1 < len(raw):
                nxt = raw[i + 1].split()
                if l and nxt and (l[-1] in ("and", "&") or (nxt[0] in WORD_NUM and any(w in MINOR for w in nxt))):
                    l = l + nxt
                    i += 1
            lines.append(l)
            i += 1
        for toks in lines:
            if not any(w in WORD_NUM or w in WORD_MUL for w in toks):
                continue
            toks = [w for w in toks if w not in MAJOR]
            minor_idx = next((i for i, w in enumerate(toks) if w in MINOR), None)
            major_words, minor_words = [], []
            if minor_idx is not None:
                and_idx = max((i for i, w in enumerate(toks[:minor_idx]) if w in ("and", "&")), default=None)
                if and_idx is not None:
                    major_words = [w for w in toks[:and_idx] if w in WORD_NUM or w in WORD_MUL or w in ("and", "&")]
                    minor_words = [w for w in toks[and_idx + 1:minor_idx] if w in WORD_NUM or w in WORD_MUL]
            if not major_words:
                started = False
                for w in toks:
                    if w in WORD_NUM or w in WORD_MUL:
                        major_words.append(w)
                        started = True
                    elif w in ("and", "&") and started:
                        major_words.append(w)
                    elif started:
                        break
            major = _words_to_number(major_words) if major_words else None
            if major is None or major <= 0:
                continue
            minor = _words_to_number(minor_words) if minor_words else None
            if minor is not None and 0 <= minor < 100:
                return round(major + minor / 100.0, 2)
            return float(major)
    return None


def read_image_amount(path: Path, cache: Optional[OCRCache] = None) -> tuple[Optional[float], str]:
    if not path.exists():
        return None, "image file absent"
    texts = _ocr_passes(path, cache)
    if not texts:
        return None, "no OCR text available (tesseract not installed and no cached OCR)"
    v, why = amount_from_ocr(texts)
    w = amount_from_words(texts)
    if w is not None and w > 0:
        if v is None or abs(v - w) > 0.011:
            # digits and words disagree: the written amount is more robust to symbol misreads
            return w, f"amount in words ({w}); digits read {v} [{why}]"
        return v, why + " (confirmed by words)"
    return v, why


# ----------------------------------------------------------------------------
# optional LLM layer (Anthropic Messages API)
# ----------------------------------------------------------------------------
class LLMExtractor:
    """Thin client around the Anthropic Messages API with a JSON cache and token accounting."""

    def __init__(self, cache_dir: Path, prompts_dir: Path, model: str = "claude-sonnet-4-5"):
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        self.model = os.environ.get("BUYORWAIT_MODEL", model)
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = cache_dir / "llm_cache.json"
        self.cache = json.loads(self.cache_file.read_text()) if self.cache_file.exists() else {}
        self.prompts_dir = prompts_dir
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_hits = 0

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _call(self, system: str, content: list) -> Optional[str]:
        key = hashlib.sha256(json.dumps([self.model, system, content], sort_keys=True, default=str).encode()).hexdigest()
        if key in self.cache:
            self.cache_hits += 1
            return self.cache[key]["text"]
        if not self.enabled:
            return None
        import urllib.request

        body = json.dumps({"model": self.model, "max_tokens": 600, "temperature": 0, "system": system,
                           "messages": [{"role": "user", "content": content}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
            "content-type": "application/json", "x-api-key": self.key, "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage", {})
        self.calls += 1
        self.input_tokens += int(usage.get("input_tokens", 0))
        self.output_tokens += int(usage.get("output_tokens", 0))
        self.cache[key] = {"text": text, "usage": usage, "model": self.model}
        self.cache_file.write_text(json.dumps(self.cache, indent=1))
        return text

    def _prompt(self, name: str) -> str:
        return (self.prompts_dir / name).read_text()

    def read_message(self, text: str) -> Optional[dict]:
        out = self._call(self._prompt("message_extraction.md"), [{"type": "text", "text": text}])
        return _parse_json(out) if out else None

    def read_image(self, path: Path, description: str) -> Optional[dict]:
        if not path.exists():
            return None
        b64 = base64.b64encode(path.read_bytes()).decode()
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                   {"type": "text", "text": f"Event description: {description}"}]
        out = self._call(self._prompt("image_extraction.md"), content)
        return _parse_json(out) if out else None

    def usage(self) -> dict:
        return dict(model=self.model, calls=self.calls, cache_hits=self.cache_hits, input_tokens=self.input_tokens,
                    output_tokens=self.output_tokens, enabled=self.enabled)


def _parse_json(text: str) -> Optional[dict]:
    try:
        text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
        return json.loads(text)
    except Exception:
        return None


LLM_KINDS = {k for k, _, _ in TEMPLATES} | {"unknown"}


def merge_llm_message(reading: MessageReading, llm: Optional[dict]) -> MessageReading:
    """Use the LLM reading only when the rules found nothing and the LLM output is well-formed."""
    if reading.kind != "unknown" or not llm or llm.get("kind") not in LLM_KINDS:
        return reading
    facts = []
    for f in llm.get("facts", []) or []:
        if not isinstance(f, dict) or f.get("type") not in {"salary_amount", "salary_date", "income_stopped", "unconfirmed_income",
                                                              "one_time_credit", "one_time_debit", "rent_change", "internal_transfer"}:
            continue
        if "date" in f and f["date"]:
            try:
                f["date"] = date.fromisoformat(str(f["date"])[:10])
            except ValueError:
                f.pop("date")
        if "amount" in f:
            try:
                f["amount"] = float(f["amount"])
            except (TypeError, ValueError):
                continue
        facts.append(f)
    return MessageReading(reading.message_id, str(llm["kind"]), facts, str(llm.get("summary", ""))[:200], source="llm")
