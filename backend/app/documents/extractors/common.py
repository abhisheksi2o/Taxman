"""Shared helpers for rule-based extractors."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

AMOUNT_RE = r"(-?\(?[\d,]+(?:\.\d{1,2})?\)?)"
DATE_FORMATS = ("%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %b %Y", "%d-%B-%Y", "%d %B %Y")


def parse_amount(text: str | None) -> Decimal | None:
    if text is None:
        return None
    s = str(text).strip().replace("Rs.", "").replace("₹", "").replace(",", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if s in ("", "-", "--"):
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return -d if neg else d


def parse_date(text: str | None) -> date | None:
    if not text:
        return None
    s = str(text).strip()
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def find_amount(text: str, label_pattern: str, flags: int = re.IGNORECASE) -> Decimal | None:
    m = re.search(label_pattern + r"\s*:?\s*(?:Rs\.?|₹)?\s*" + AMOUNT_RE, text, flags)
    return parse_amount(m.group(1)) if m else None


def find_text(text: str, pattern: str, flags: int = re.IGNORECASE) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def after_anchor(text: str, anchor: str) -> str:
    idx = text.find(anchor)
    return text[idx:] if idx >= 0 else text


@dataclass
class ExtractionResult:
    fields: dict[str, Any] = field(default_factory=dict)  # normalized, human-readable summary
    observations: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    flags: list[str] = field(default_factory=list)
    period_label: str | None = None
    required_found: int = 0
    required_total: int = 0

    def score(self, found: int, total: int, penalty: float = 0.0) -> None:
        self.required_found, self.required_total = found, total
        base = (found / total) if total else 0.0
        self.confidence = round(max(0.0, min(1.0, base * 0.9 + 0.1 - penalty)), 2)


def obs(category: str, **kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"category": category}
    for k, v in kw.items():
        if isinstance(v, Decimal):
            v = float(v)
        elif isinstance(v, date):
            v = v.isoformat()
        d[k] = v
    return d


def normalize_key(name: str | None) -> str:
    if not name:
        return ""
    s = re.sub(r"[^A-Z0-9 ]", "", name.upper())
    for suffix in (" PRIVATE LIMITED", " PVT LTD", " PVT LIMITED", " LIMITED", " LTD", " LLP", " INC", " CO"):
        s = s.replace(suffix, "")
    return re.sub(r"\s+", "", s)


def quarter_for(d: date) -> str:
    return {4: "Q1", 5: "Q1", 6: "Q1", 7: "Q2", 8: "Q2", 9: "Q2", 10: "Q3", 11: "Q3", 12: "Q3", 1: "Q4", 2: "Q4", 3: "Q4"}[d.month]


def mask_account(number: str | None) -> str | None:
    if not number:
        return None
    digits = "".join(ch for ch in str(number) if ch.isalnum())
    if len(digits) <= 4:
        return digits
    return "X" * min(6, len(digits) - 4) + digits[-4:]
