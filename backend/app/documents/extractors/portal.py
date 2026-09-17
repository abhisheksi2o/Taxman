"""Extractors for e-filing portal exports (JSON): AIS, TIS, previous ITR, and investment proofs."""
from __future__ import annotations

import json
from decimal import Decimal

from .common import ExtractionResult, normalize_key, obs, parse_amount, parse_date

AIS_CATEGORY_MAP = {
    "salary": ("SALARY", None),
    "interest from savings bank": ("INTEREST", "SAVINGS"),
    "interest from deposit": ("INTEREST", "FIXED_DEPOSIT"),
    "interest from others": ("INTEREST", "OTHER"),
    "interest from income tax refund": ("INTEREST", "INCOME_TAX_REFUND"),
    "dividend": ("DIVIDEND", None),
    "sale of securities and units of mutual fund": ("SECURITIES_SALE", None),
    "purchase of securities and units of mutual funds": ("SECURITIES_PURCHASE", None),
    "rent received": ("RENT", None),
    "business receipts": ("BUSINESS_RECEIPTS", None),
    "receipts from professional fees": ("BUSINESS_RECEIPTS", None),
}


def _load_json(data: str) -> dict:
    return json.loads(data)


def extract_ais(data: str) -> ExtractionResult:
    r = ExtractionResult()
    f = r.fields
    doc = _load_json(data)
    f["pan"] = doc.get("pan")
    f["assessment_year"] = doc.get("assessment_year")
    f["financial_year"] = doc.get("financial_year")
    items = doc.get("information", []) or []
    tds_items = doc.get("tds_tcs", []) or []
    summary: dict[str, float] = {}
    for it in items:
        cat = str(it.get("category", "")).strip()
        mapped, sub = AIS_CATEGORY_MAP.get(cat.lower(), ("OTHER", None))
        amt = float(parse_amount(str(it.get("amount", 0))) or 0)
        summary[cat] = round(summary.get(cat, 0.0) + amt, 2)
        r.observations.append(obs(f"AIS_{mapped}", ais_id=it.get("id"), ais_category=cat, sub_kind=it.get("sub_kind") or sub, counterparty=it.get("source"),
                                  counterparty_tan=it.get("source_tan"), counterparty_key=it.get("source_tan") or normalize_key(it.get("source")),
                                  account_ref=it.get("account"), isin=it.get("isin"), amount=amt, tax_deducted=float(parse_amount(str(it.get("tds", 0))) or 0),
                                  date=it.get("date"), period=it.get("period"), quantity=it.get("quantity"), reference=f"AIS item {it.get('id')}",
                                  feedback=it.get("feedback_status", "Not submitted"), confidence=0.95))
    for t in tds_items:
        r.observations.append(obs("TDS", counterparty=t.get("deductor"), counterparty_tan=t.get("tan"), counterparty_key=t.get("tan") or normalize_key(t.get("deductor")),
                                  section=str(t.get("section", "")).replace("-", ""), amount=float(parse_amount(str(t.get("amount_paid", 0))) or 0),
                                  tax_deducted=float(parse_amount(str(t.get("tax_deducted", 0))) or 0), period=t.get("quarter"),
                                  reference=f"AIS Part B · {t.get('deductor')} · {t.get('section')}", confidence=0.95))
    f["category_totals"] = summary
    f["information_count"] = len(items)
    f["tds_entries"] = len(tds_items)
    r.score(sum(1 for k in ("pan", "assessment_year") if f.get(k)) + (1 if items or tds_items else 0), 3)
    r.period_label = f"FY {f['financial_year']}" if f.get("financial_year") else None
    return r


def extract_tis(data: str) -> ExtractionResult:
    r = ExtractionResult()
    f = r.fields
    doc = _load_json(data)
    f["pan"] = doc.get("pan")
    f["assessment_year"] = doc.get("assessment_year")
    cats = doc.get("categories", []) or []
    f["categories"] = cats
    for c in cats:
        r.observations.append(obs("TIS_SUMMARY", tis_category=c.get("category"), processed_value=float(parse_amount(str(c.get("processed_value", 0))) or 0),
                                  amount=float(parse_amount(str(c.get("derived_value", 0))) or 0), reference=f"TIS · {c.get('category')}", confidence=0.95))
    r.score(1 if cats else 0, 1)
    return r


def extract_previous_itr(data: str) -> ExtractionResult:
    r = ExtractionResult()
    f = r.fields
    doc = _load_json(data)
    for k in ("assessment_year", "itr_form", "regime", "filed_on", "acknowledgement_number"):
        f[k] = doc.get(k)
    for k in ("gross_total_income", "total_deductions", "taxable_income", "total_tax", "tds", "refund_or_payable"):
        f[k] = float(parse_amount(str(doc.get(k, 0))) or 0)
    f["income_heads"] = {k: float(parse_amount(str(v)) or 0) for k, v in (doc.get("income_heads") or {}).items()}
    r.score(sum(1 for k in ("assessment_year", "itr_form") if f.get(k)) + (1 if f["gross_total_income"] else 0), 3)
    for head, amt in f["income_heads"].items():
        r.observations.append(obs("PREV_YEAR", head=head, amount=amt, period=f["assessment_year"], reference=f"ITR {f['assessment_year']} · {head}", confidence=0.95))
    r.observations.append(obs("PREV_YEAR_SUMMARY", amount=f["gross_total_income"], total_tax=f["total_tax"], tds=f["tds"], refund_or_payable=f["refund_or_payable"],
                              regime=f.get("regime"), period=f["assessment_year"], reference=f"ITR {f['assessment_year']} · summary"))
    return r


def extract_investment_proof(data: str) -> ExtractionResult:
    r = ExtractionResult()
    f = r.fields
    doc = _load_json(data)
    items = doc.get("items", []) or []
    f["items"] = items
    f["total"] = round(sum(float(parse_amount(str(i.get("amount", 0))) or 0) for i in items), 2)
    r.score(1 if items else 0, 1)
    for i in items:
        r.observations.append(obs("INVESTMENT", instrument=i.get("instrument"), counterparty=i.get("provider"), amount=float(parse_amount(str(i.get("amount", 0))) or 0),
                                  date=i.get("date"), section_hint=i.get("section"), reference=f"Proof · {i.get('instrument')} · {i.get('provider')}", confidence=0.9))
    return r
