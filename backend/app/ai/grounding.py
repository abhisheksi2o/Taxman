"""Grounding guard: every rupee figure Astra states must exist in the structured model / tool outputs."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from app.models.common import money
from app.tax_engine.engine import fmt

AMOUNT_RE = re.compile(r"(?:₹|Rs\.?)\s?((?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d{1,2})?)")
AMOUNT_KEYS = {"amount", "tax_deducted", "impact_estimate", "gross", "cost", "principal", "processed_value", "derived_value", "credit", "debit", "balance", "interest",
               "tds", "sell_value", "buy_value", "amount_paid", "total_tds", "total_interest", "total_amount_paid", "gross_salary", "used", "difference", "old", "new",
               "value", "total", "expected_impact", "correct_impact", "astra_impact"}


def _add(values: set[str], v: Any) -> None:
    try:
        d = money(v)
    except Exception:  # noqa: BLE001
        return
    s = fmt(abs(d)).lstrip("₹-")
    values.add(s)
    values.add(s.split(".")[0])


def collect_amounts(obj: Any, values: set[str] | None = None) -> set[str]:
    """Walk any JSON-like structure and collect every numeric value (formatted Indian-style)."""
    values = set() if values is None else values
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
                _add(values, v)
            elif isinstance(v, str) and k in AMOUNT_KEYS:
                _add(values, v.replace(",", "").replace("₹", ""))
            else:
                collect_amounts(v, values)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            collect_amounts(v, values)
    elif isinstance(obj, (int, float, Decimal)) and not isinstance(obj, bool):
        _add(values, obj)
    elif isinstance(obj, str):
        for m in AMOUNT_RE.finditer(obj):
            raw = m.group(1)
            values.add(raw)
            values.add(raw.split(".")[0])
    return values


def unverified_amounts(text: str, grounded: set[str]) -> list[str]:
    out = []
    for m in AMOUNT_RE.finditer(text):
        raw = m.group(1).rstrip(".")
        whole = raw.split(".")[0]
        if raw not in grounded and whole not in grounded:
            out.append("₹" + raw)
    return list(dict.fromkeys(out))
