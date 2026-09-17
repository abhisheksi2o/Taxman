"""Document type detection from content (and an optional user hint)."""
from __future__ import annotations

import json
import re

from app.models.common import SourceType

SIGNATURES: list[tuple[SourceType, list[tuple[str, float]]]] = [
    (SourceType.FORM16A, [(r"FORM NO\.?\s*16A", 3.0), (r"rule 31\(1\)\(b\)", 1.5), (r"Deductee", 1.0)]),
    (SourceType.FORM16, [(r"FORM NO\.?\s*16(?!A)", 3.0), (r"rule 31\(1\)\(a\)", 1.5), (r"Gross Salary", 1.0), (r"PART B", 0.5)]),
    (SourceType.FORM26AS, [(r"Form 26AS", 3.0), (r"Annual Tax Statement", 2.0), (r"PART-I", 0.5)]),
    (SourceType.AIS, [(r"Annual Information Statement", 3.0), (r'"information"', 1.0)]),
    (SourceType.TIS, [(r"Taxpayer Information Summary", 3.0), (r"derived_value", 1.0)]),
    (SourceType.INTEREST_CERTIFICATE, [(r"Interest Certificate", 3.0), (r"Interest Paid\s*/\s*Accrued", 1.5)]),
    (SourceType.SALARY_SLIP, [(r"PAYSLIP|Salary Slip|Pay Slip", 3.0), (r"Net Pay", 1.0), (r"Gross Earnings", 1.0)]),
    (SourceType.HOME_LOAN_CERTIFICATE, [(r"Home Loan Interest Certificate|Provisional Interest Certificate", 3.0), (r"Principal repaid", 1.0)]),
    (SourceType.RENT_RECEIPT, [(r"RENT RECEIPT", 3.0), (r"towards rent", 1.0)]),
    (SourceType.BANK_STATEMENT, [(r"Statement of Account|Account Statement|Bank Statement", 2.5), (r"Narration|Particulars|Description", 1.0), (r"Balance", 0.5), (r"Withdrawal|Debit", 0.5), (r"Deposit|Credit", 0.5)]),
    (SourceType.BROKER_STATEMENT, [(r"Buy Date", 2.0), (r"Sell Date", 2.0), (r"Tax P&L|Capital Gains Statement|Realised P&L|Trade Ref", 1.0)]),
    (SourceType.CAPITAL_GAINS_STATEMENT, [(r"Capital Gains Statement", 3.0), (r"Sell Date", 1.0)]),
    (SourceType.DIVIDEND_STATEMENT, [(r"Dividend Per Share", 3.0), (r"Dividend Statement", 2.0)]),
    (SourceType.PREVIOUS_ITR, [(r"INDIAN INCOME TAX RETURN|ITR-V|Acknowledgement", 3.0), (r'"itr_form"', 2.0)]),
    (SourceType.INVESTMENT_PROOF, [(r"Investment proof|Investment Proof", 3.0), (r'"instrument"', 1.5)]),
]


def detect_type(text: str, filename: str = "", hint: SourceType | None = None) -> tuple[SourceType, float]:
    sample = text[:20000]
    scores: dict[SourceType, float] = {}
    json_doc = None
    try:
        json_doc = json.loads(sample) if sample.lstrip().startswith("{") else None
    except json.JSONDecodeError:
        json_doc = None
    if isinstance(json_doc, dict) and json_doc.get("document"):
        name = str(json_doc["document"]).lower()
        mapping = {"annual information statement": SourceType.AIS, "taxpayer information summary": SourceType.TIS,
                   "investment proof": SourceType.INVESTMENT_PROOF}
        for k, v in mapping.items():
            if k in name:
                return v, 0.99
        if "itr" in name or "acknowledgement" in name:
            return SourceType.PREVIOUS_ITR, 0.99
    for stype, sigs in SIGNATURES:
        s = 0.0
        for pattern, weight in sigs:
            if re.search(pattern, sample, re.I):
                s += weight
        if s:
            scores[stype] = s
    fname = filename.lower()
    filename_hints = {"form16a": SourceType.FORM16A, "form_16a": SourceType.FORM16A, "form16": SourceType.FORM16, "form_16": SourceType.FORM16,
                      "26as": SourceType.FORM26AS, "ais": SourceType.AIS, "tis": SourceType.TIS, "bank": SourceType.BANK_STATEMENT,
                      "interest": SourceType.INTEREST_CERTIFICATE, "payslip": SourceType.SALARY_SLIP, "salary": SourceType.SALARY_SLIP,
                      "broker": SourceType.BROKER_STATEMENT, "pnl": SourceType.BROKER_STATEMENT, "dividend": SourceType.DIVIDEND_STATEMENT,
                      "loan": SourceType.HOME_LOAN_CERTIFICATE, "itr": SourceType.PREVIOUS_ITR, "rent": SourceType.RENT_RECEIPT}
    for k, v in filename_hints.items():
        if k in fname:
            scores[v] = scores.get(v, 0.0) + 1.0
    if hint:
        scores[hint] = scores.get(hint, 0.0) + 2.5
    if not scores:
        return (hint or SourceType.OTHER), (0.5 if hint else 0.0)
    best = max(scores.items(), key=lambda kv: kv[1])
    total = sum(scores.values())
    conf = round(min(0.99, 0.5 + 0.5 * (best[1] / total) * min(1.0, best[1] / 3.0)), 2)
    return best[0], conf
