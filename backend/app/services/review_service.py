"""Before-You-File + final return review, shared by the HTTP API and the in-browser backend."""
from __future__ import annotations

import json
from decimal import Decimal

from app.models.common import ValueStatus, mask_pan, money, new_id, utcnow
from app.models.tax_model import TaxCase
from app.readiness.checker import check
from app.readiness.requirements import requirements_for
from app.reconciliation.impact import working_regime
from app.services.errors import ServiceError
from app.services.views import profile_view
from app.tax_engine.engine import compute
from app.tax_engine.rules import get_rules

DECLARATIONS = [
    {"code": "reviewed_income", "text": "I have reviewed every income entry and its source, and confirm it is complete to the best of my knowledge."},
    {"code": "reviewed_deductions", "text": "The deductions and exemptions claimed are supported by documents in my possession."},
    {"code": "reviewed_tds", "text": "I have checked the TDS / tax credits against Form 26AS."},
    {"code": "regime_choice", "text": "I have compared both regimes and I am choosing the regime selected below."},
    {"code": "personal_details", "text": "My personal details, bank account and residential status are correct."},
    {"code": "estimate_ack", "text": "I understand ASTRA Tax prepares an estimate under versioned rules and does not file the return or provide legal advice; unresolved items may need professional review."},
]


def itr_form(case: TaxCase, comp) -> tuple[str, list[str]]:
    rules = get_rules(case.assessment_year)
    regime = working_regime(case)
    s = comp.regime(regime).summary
    reasons = []
    total = s.taxable_income
    has_business = bool(case.income.business)
    presumptive = has_business and all(b.nature in ("PROFESSION_44ADA", "BUSINESS_44AD") for b in case.income.business)
    cg_line = next((l for l in comp.regime(regime).lines if l.id == "cg"), None)
    ltcg_bucket_total = sum((money(c.amount) for c in (cg_line.children if cg_line else []) if c.id.startswith("cg.bucket.LTCG_112A")), Decimal("0"))
    stcg_present = any(c.id.startswith("cg.bucket.STCG") for c in (cg_line.children if cg_line else []))
    let_out = [r for r in case.income.rental if not r.is_self_occupied]
    if case.taxpayer.has_foreign_income_or_assets:
        reasons.append("foreign income / assets → ITR-2 or ITR-3")
        return ("ITR-3" if has_business else "ITR-2"), reasons
    if has_business and not presumptive:
        reasons.append("regular business income → ITR-3")
        return "ITR-3", reasons
    if presumptive:
        if total <= rules.itr1_income_limit and len(let_out) <= 1 and not stcg_present and ltcg_bucket_total <= rules.itr1_ltcg_112a_limit:
            reasons.append("presumptive income u/s 44AD/44ADA with income up to ₹50 lakh → ITR-4 (Sugam)")
            return "ITR-4", reasons
        reasons.append("presumptive income but other conditions of ITR-4 not met → ITR-3")
        return "ITR-3", reasons
    if total <= rules.itr1_income_limit and len(let_out) <= 1 and not stcg_present and ltcg_bucket_total <= rules.itr1_ltcg_112a_limit and len(case.income.rental) <= 1:
        reasons.append("salary / one house property / other sources (and LTCG u/s 112A up to ₹1.25 lakh) with income up to ₹50 lakh → ITR-1 (Sahaj)")
        return "ITR-1", reasons
    reasons.append("capital gains beyond the ITR-1 limit, more than one house property, or income above ₹50 lakh → ITR-2")
    return "ITR-2", reasons


def _status_counts(case: TaxCase) -> dict:
    counts: dict[str, int] = {}
    for e in case.income.all_entities() + case.deductions + case.investments + case.loans + case.tax_deducted:
        st = getattr(e, "status", None)
        if st:
            counts[st.value] = counts.get(st.value, 0) + 1
    return counts


def build_package(case: TaxCase, comp, report) -> dict:
    regime = case.review.selected_regime or working_regime(case)
    rc = comp.regime(regime)
    itr, reasons = itr_form(case, comp)
    tp = case.taxpayer

    def section(top_id):
        line = next((l for l in rc.lines if l.id == top_id), None)
        return line.model_dump(mode="json") if line else None

    return {
        "assessment_year": case.assessment_year, "financial_year": comp.financial_year, "rules_version": comp.rules_version, "prepared_at": utcnow().isoformat(),
        "regime": regime, "regime_label": rc.label, "itr_form": itr, "itr_form_reasons": reasons,
        "personal_details": {"name": tp.name, "pan_masked": mask_pan(tp.pan), "date_of_birth": tp.date_of_birth.isoformat() if tp.date_of_birth else None, "residential_status": tp.residential_status.value,
                             "taxpayer_type": tp.taxpayer_type, "email": tp.email, "phone": tp.phone, "address": tp.address, "status": tp.field_status.get("name", ValueStatus.USER_ENTERED).value},
        "bank_details": [b.model_dump(mode="json") for b in case.bank_accounts],
        "income": {k: section(k) for k in ("salary", "hp", "cg", "business", "os")},
        "gross_total_income": section("gti"), "deductions": section("via"), "taxable_income": section("taxable_income"),
        "tax": [l.model_dump(mode="json") for l in rc.lines if l.id.startswith("tax.")], "credits": section("credits"), "interest": section("interest"), "result": section("net"),
        "summary": rc.summary.model_dump(mode="json"), "comparison": comp.comparison.model_dump(mode="json"),
        "documents": [{"id": d.id, "filename": d.filename, "type": d.type.value, "status": d.status, "confidence": d.extraction_confidence} for d in case.documents],
        "open_items": [i.model_dump(mode="json") for i in case.open_items()], "resolved_items": [{"id": i.id, "title": i.title, "status": i.status, "note": i.resolution_note} for i in case.reconciliation_items if i.status != "OPEN"],
        "readiness": report.model_dump(mode="json"), "declarations": DECLARATIONS,
        "value_status_counts": _status_counts(case),
        "disclaimer": "ASTRA Tax prepares an estimate using versioned official rules. It does not file the return, is not a substitute for a tax professional, and unresolved items should be reviewed before filing.",
    }


def review_view(case: TaxCase) -> dict:
    comp = compute(case)
    report = check(case, comp)
    return {"readiness": report.model_dump(mode="json"), "requirements": [r.as_dict() for r in requirements_for(case)], "package": build_package(case, comp, report),
            "review_state": case.review.model_dump(mode="json"), "profile": profile_view(case)}


def package_key(case: TaxCase) -> str:
    return f"{case.id}/packages/{case.review.return_package_id}.json"


def confirm_review(case: TaxCase, declarations: dict[str, bool], selected_regime: str, acknowledge_open_issues: bool, store) -> tuple[dict, int, int]:
    """Validates declarations / open items, marks the review confirmed and stores the package. Returns
    (package, high_priority_open_count, readiness_score)."""
    regime = (selected_regime or "").upper()
    if regime not in ("OLD", "NEW"):
        raise ServiceError(400, "selected_regime must be OLD or NEW")
    missing = [d["code"] for d in DECLARATIONS if not declarations.get(d["code"])]
    if missing:
        raise ServiceError(400, f"All declarations must be confirmed: {', '.join(missing)}")
    comp = compute(case)
    report = check(case, comp)
    high = [i for i in case.open_items() if i.severity == "HIGH"]
    if high and not acknowledge_open_issues:
        raise ServiceError(409, f"{len(high)} high-priority item(s) are still open. Resolve them or explicitly acknowledge them.")
    if report.blocking and "Profile" in report.blocking:
        raise ServiceError(409, "Profile details are incomplete (name / PAN).")
    case.review.declarations = {d["code"]: True for d in DECLARATIONS}
    case.review.selected_regime = regime
    case.review.acknowledged_open_issues = acknowledge_open_issues
    case.review.confirmed = True
    case.review.confirmed_at = utcnow()
    case.review.return_package_id = new_id("pkg")
    package = build_package(case, comp, report)
    package["confirmation"] = {"confirmed_at": case.review.confirmed_at.isoformat(), "package_id": case.review.return_package_id, "acknowledged_open_issues": acknowledge_open_issues}
    store.put(package_key(case), json.dumps(package, default=str).encode())
    return package, len(high), report.score


def reset_review(case: TaxCase) -> None:
    case.review.confirmed = False
    case.review.confirmed_at = None
    case.review.declarations = {}


CONFIRM_NOTE = "The return package has been prepared. ASTRA Tax does not submit anything to the e-filing portal – filing is a separate, explicit step outside this prototype."
