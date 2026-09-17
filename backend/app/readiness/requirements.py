"""Requirement engine: what information / documents does this taxpayer still need?

Deterministic rules derived from the onboarding profile and the current model. Used by onboarding
(next step), the dashboard, the Before-You-File check and Ask Astra ("what documents are still required?").
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models.common import SourceType
from app.models.tax_model import TaxCase


@dataclass
class RequiredItem:
    code: str
    kind: str  # DOCUMENT | INFORMATION
    label: str
    why: str
    priority: str  # REQUIRED | RECOMMENDED | OPTIONAL
    status: str = "MISSING"  # SATISFIED | MISSING
    satisfied_by: list[str] = field(default_factory=list)
    document_type: str | None = None

    def as_dict(self) -> dict:
        return self.__dict__


def _has_doc(case: TaxCase, *types: SourceType) -> list[str]:
    return [d.id for d in case.documents if d.type in types and d.status in ("EXTRACTED", "NEEDS_REVIEW")]


def requirements_for(case: TaxCase) -> list[RequiredItem]:
    tp = case.taxpayer
    items: list[RequiredItem] = []
    sources = set(tp.income_sources)

    def add(code, kind, label, why, priority, satisfied: list[str] | bool, document_type=None):
        ok = bool(satisfied)
        items.append(RequiredItem(code=code, kind=kind, label=label, why=why, priority=priority, status="SATISFIED" if ok else "MISSING",
                                  satisfied_by=(satisfied if isinstance(satisfied, list) else []), document_type=document_type))

    # ---- profile information
    add("profile.name", "INFORMATION", "Name", "Required on the return", "REQUIRED", bool(tp.name))
    add("profile.pan", "INFORMATION", "PAN", "Identifies the taxpayer", "REQUIRED", bool(tp.pan))
    add("profile.dob", "INFORMATION", "Date of birth", "Determines age-based exemption limits", "REQUIRED", bool(tp.date_of_birth))
    add("profile.residential_status", "INFORMATION", "Residential status", "Decides which income is taxable in India", "REQUIRED", tp.status_of("residential_status").value in ("USER_CONFIRMED", "USER_ENTERED") and bool(tp.residential_status))
    add("profile.income_sources", "INFORMATION", "Income sources", "Drives which documents are needed", "REQUIRED", bool(tp.income_sources))
    add("profile.bank", "INFORMATION", "Bank account for refund", "A pre-validated bank account is required to receive a refund", "REQUIRED", [b.id for b in case.bank_accounts])
    # ---- salary
    if "SALARY" in sources or tp.employment_status in ("SALARIED", "BOTH"):
        n = tp.employer_count or 1
        f16 = _has_doc(case, SourceType.FORM16)
        add("doc.form16", "DOCUMENT", f"Form 16 ({n} employer{'s' if n > 1 else ''})", "Certifies salary and TDS u/s 192", "REQUIRED", f16 if len(f16) >= n else [], "FORM16")
        add("doc.salary_slips", "DOCUMENT", "Salary slips", "Help verify allowances and HRA", "OPTIONAL", _has_doc(case, SourceType.SALARY_SLIP), "SALARY_SLIP")
    # ---- interest
    if "INTEREST" in sources or case.income.interest:
        add("doc.interest_certificate", "DOCUMENT", "Interest certificate(s)", "Bank confirmation of interest paid / accrued and TDS", "RECOMMENDED", _has_doc(case, SourceType.INTEREST_CERTIFICATE), "INTEREST_CERTIFICATE")
        add("doc.bank_statement", "DOCUMENT", "Bank statement(s)", "Cross-checks interest credits and other receipts", "RECOMMENDED", _has_doc(case, SourceType.BANK_STATEMENT), "BANK_STATEMENT")
    # ---- capital gains / dividends
    if "CAPITAL_GAINS" in sources or case.income.capital_gains:
        add("doc.broker", "DOCUMENT", "Broker tax P&L / capital-gains statement", "Trade-level data for short/long-term classification", "REQUIRED", _has_doc(case, SourceType.BROKER_STATEMENT, SourceType.CAPITAL_GAINS_STATEMENT), "BROKER_STATEMENT")
    if "DIVIDEND" in sources or case.income.dividend:
        add("doc.dividend", "DOCUMENT", "Dividend statement", "Dividend received and TDS u/s 194", "RECOMMENDED", _has_doc(case, SourceType.DIVIDEND_STATEMENT), "DIVIDEND_STATEMENT")
    # ---- rental
    if "RENTAL" in sources or tp.has_rental_income or any(not r.is_self_occupied for r in case.income.rental):
        add("info.rent", "INFORMATION", "Rent received & municipal taxes", "Needed to compute income from house property", "REQUIRED", [r.id for r in case.income.rental if not r.is_self_occupied and r.annual_rent_received.amount > 0])
    if tp.has_home_loan or case.loans:
        add("doc.home_loan", "DOCUMENT", "Home-loan interest certificate", "Interest deduction u/s 24(b) and principal u/s 80C", "REQUIRED", _has_doc(case, SourceType.HOME_LOAN_CERTIFICATE) or [l.id for l in case.loans if l.interest_paid.amount > 0], "HOME_LOAN_CERTIFICATE")
    # ---- business
    if "BUSINESS" in sources or "FREELANCE" in sources or case.income.business:
        add("info.receipts", "INFORMATION", "Gross receipts / turnover", "Needed for presumptive income u/s 44ADA / 44AD", "REQUIRED", [b.id for b in case.income.business if b.gross_receipts.amount > 0])
        add("doc.form16a", "DOCUMENT", "Form 16A from clients", "Certifies TDS u/s 194J / 194H", "RECOMMENDED", _has_doc(case, SourceType.FORM16A), "FORM16A")
    # ---- investments / deductions
    if tp.has_investments or case.investments or case.deductions:
        add("doc.investment_proof", "DOCUMENT", "Investment / insurance proofs", "Support Chapter VI-A claims (80C, 80D…)", "RECOMMENDED", _has_doc(case, SourceType.INVESTMENT_PROOF) or [i.id for i in case.investments], "INVESTMENT_PROOF")
    # ---- always
    add("doc.ais", "DOCUMENT", "Annual Information Statement (AIS)", "Department's view of your income – reconciled against your documents", "REQUIRED", _has_doc(case, SourceType.AIS), "AIS")
    add("doc.26as", "DOCUMENT", "Form 26AS", "Tax credits actually available", "REQUIRED", _has_doc(case, SourceType.FORM26AS), "FORM26AS")
    add("doc.tis", "DOCUMENT", "Taxpayer Information Summary (TIS)", "Category totals for a final cross-check", "OPTIONAL", _has_doc(case, SourceType.TIS), "TIS")
    if tp.filed_previous_return or case.previous_return:
        add("doc.prev_itr", "DOCUMENT", "Previous year's return", "Enables year-on-year comparison", "OPTIONAL", _has_doc(case, SourceType.PREVIOUS_ITR) or ([case.previous_return.assessment_year] if case.previous_return else []), "PREVIOUS_ITR")
    if tp.has_foreign_income_or_assets:
        add("info.foreign", "INFORMATION", "Foreign income / assets details", "Schedule FA / FSI must be filled; professional review recommended", "REQUIRED", False)
    return items


ONBOARDING_STEPS = [
    {"id": "assessment_year", "title": "Which return are we preparing?", "fields": ["assessment_year"]},
    {"id": "identity", "title": "About you", "fields": ["name", "pan", "date_of_birth", "taxpayer_type", "residential_status"]},
    {"id": "employment", "title": "Your work", "fields": ["employment_status"]},
    {"id": "income_sources", "title": "Where does your income come from?", "fields": ["income_sources"]},
    {"id": "details", "title": "A few details", "fields": []},  # dynamic
    {"id": "history", "title": "Last year and preferences", "fields": ["filed_previous_return", "regime_preference"]},
]


def next_onboarding_step(case: TaxCase) -> dict:
    """Progressive disclosure: return the next step whose fields are incomplete, with dynamic detail fields."""
    tp = case.taxpayer
    dynamic: list[dict] = []
    if "SALARY" in tp.income_sources:
        dynamic.append({"name": "employer_count", "label": "How many employers did you have during the year?", "type": "number", "value": tp.employer_count})
        dynamic.append({"name": "city_type", "label": "Do you live in a metro city (for HRA)?", "type": "select", "options": ["METRO", "NON_METRO"], "value": tp.city_type})
    if "RENTAL" in tp.income_sources:
        dynamic.append({"name": "property_count", "label": "How many let-out properties?", "type": "number", "value": tp.property_count})
    dynamic.append({"name": "has_investments", "label": "Did you invest in tax-saving instruments (ELSS, PPF, NPS, insurance)?", "type": "boolean", "value": tp.has_investments})
    dynamic.append({"name": "has_home_loan", "label": "Are you repaying a home loan?", "type": "boolean", "value": tp.has_home_loan})
    if tp.residential_status == "RESIDENT" or getattr(tp.residential_status, "value", "") == "RESIDENT":
        dynamic.append({"name": "has_foreign_income_or_assets", "label": "Do you hold foreign assets or earn foreign income?", "type": "boolean", "value": tp.has_foreign_income_or_assets})
    steps = []
    for step in ONBOARDING_STEPS:
        s = dict(step)
        if step["id"] == "details":
            s["dynamic_fields"] = dynamic
            complete = all(f["value"] is not None for f in dynamic)
        else:
            complete = all(getattr(tp, f, None) not in (None, "", []) for f in step["fields"])
            if step["id"] == "history":
                complete = tp.filed_previous_return is not None and tp.regime_preference is not None
        s["complete"] = complete
        steps.append(s)
    nxt = next((s for s in steps if not s["complete"]), None)
    return {"steps": steps, "next_step": nxt["id"] if nxt else None, "completed": nxt is None,
            "astra_note": _astra_onboarding_note(tp, nxt)}


def _astra_onboarding_note(tp, nxt) -> str:
    if nxt is None:
        return "Your profile is complete. Next, upload your Form 16 / AIS / 26AS and Astra will start reconciling."
    if nxt["id"] == "income_sources":
        return "Pick every source that applies – Astra uses this to decide which documents to ask for."
    if nxt["id"] == "details":
        parts = []
        if "SALARY" in tp.income_sources:
            parts.append("the number of employers decides how many Form 16s we need")
        if "RENTAL" in tp.income_sources:
            parts.append("property details unlock the house-property computation")
        return "Just a few follow-ups: " + "; ".join(parts) + "." if parts else "A few follow-ups to tailor the checklist."
    return "Only the essentials for now – everything else is progressive."
