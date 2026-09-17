"""Before-You-File check and the final return review with explicit confirmation."""
from __future__ import annotations

import json
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.deps import current_user, get_repo, load_case
from app.api.serializers import profile_view
from app.core import audit
from app.core.storage import get_storage
from app.db.models import User
from app.db.repository import CaseRepository
from app.models.common import ValueStatus, mask_pan, money, new_id, utcnow
from app.models.tax_model import TaxCase
from app.readiness.checker import check
from app.readiness.requirements import requirements_for
from app.reconciliation.impact import working_regime
from app.tax_engine.engine import compute
from app.tax_engine.rules import get_rules

router = APIRouter(prefix="/cases/{case_id}/review", tags=["review"])

DECLARATIONS = [
    {"code": "reviewed_income", "text": "I have reviewed every income entry and its source, and confirm it is complete to the best of my knowledge."},
    {"code": "reviewed_deductions", "text": "The deductions and exemptions claimed are supported by documents in my possession."},
    {"code": "reviewed_tds", "text": "I have checked the TDS / tax credits against Form 26AS."},
    {"code": "regime_choice", "text": "I have compared both regimes and I am choosing the regime selected below."},
    {"code": "personal_details", "text": "My personal details, bank account and residential status are correct."},
    {"code": "estimate_ack", "text": "I understand ASTRA Tax prepares an estimate under versioned rules and does not file the return or provide legal advice; unresolved items may need professional review."},
]


class Confirmation(BaseModel):
    declarations: dict[str, bool]
    selected_regime: str
    acknowledge_open_issues: bool = False


def _itr_form(case: TaxCase, comp) -> tuple[str, list[str]]:
    rules = get_rules(case.assessment_year)
    s = comp.regime(working_regime(case)).summary
    reasons = []
    total = s.taxable_income
    has_business = bool(case.income.business)
    presumptive = has_business and all(b.nature in ("PROFESSION_44ADA", "BUSINESS_44AD") for b in case.income.business)
    cg = case.income.capital_gains
    ltcg_112a_only = all(t.asset_class in ("LISTED_EQUITY", "EQUITY_MF") and t.stt_paid for t in cg) if cg else True
    from app.tax_engine.engine import RegimeEngine  # noqa: F401 – classification lives in the engine lines
    cg_line = next((l for l in comp.regime(working_regime(case)).lines if l.id == "cg"), None)
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


def _package(case: TaxCase, comp, report) -> dict:
    regime = case.review.selected_regime or working_regime(case)
    rc = comp.regime(regime)
    itr, reasons = _itr_form(case, comp)
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


def _status_counts(case: TaxCase) -> dict:
    counts: dict[str, int] = {}
    for e in case.income.all_entities() + case.deductions + case.investments + case.loans + case.tax_deducted:
        st = getattr(e, "status", None)
        if st:
            counts[st.value] = counts.get(st.value, 0) + 1
    return counts


@router.get("")
def review(case: TaxCase = Depends(load_case)):
    comp = compute(case)
    report = check(case, comp)
    return {"readiness": report.model_dump(mode="json"), "requirements": [r.as_dict() for r in requirements_for(case)], "package": _package(case, comp, report),
            "review_state": case.review.model_dump(mode="json"), "profile": profile_view(case)}


@router.post("/confirm")
def confirm(body: Confirmation, case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    regime = body.selected_regime.upper()
    if regime not in ("OLD", "NEW"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "selected_regime must be OLD or NEW")
    missing = [d["code"] for d in DECLARATIONS if not body.declarations.get(d["code"])]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"All declarations must be confirmed: {', '.join(missing)}")
    comp = compute(case)
    report = check(case, comp)
    high = [i for i in case.open_items() if i.severity == "HIGH"]
    if high and not body.acknowledge_open_issues:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{len(high)} high-priority item(s) are still open. Resolve them or explicitly acknowledge them.")
    if report.blocking and "Profile" in report.blocking:
        raise HTTPException(status.HTTP_409_CONFLICT, "Profile details are incomplete (name / PAN).")
    case.review.declarations = {d["code"]: True for d in DECLARATIONS}
    case.review.selected_regime = regime
    case.review.acknowledged_open_issues = body.acknowledge_open_issues
    case.review.confirmed = True
    case.review.confirmed_at = utcnow()
    case.review.return_package_id = new_id("pkg")
    package = _package(case, comp, report)
    package["confirmation"] = {"confirmed_at": case.review.confirmed_at.isoformat(), "package_id": case.review.return_package_id, "acknowledged_open_issues": body.acknowledge_open_issues}
    get_storage().put(f"{case.id}/packages/{case.review.return_package_id}.json", json.dumps(package, default=str).encode())
    repo.save(case)
    audit.record(repo, case.id, user.id, audit.ACTOR_USER, "review.confirmed", f"User confirmed the final review ({regime.lower()} regime, {len(high)} high-priority item(s) acknowledged)",
                 {"package_id": case.review.return_package_id, "regime": regime, "readiness": report.score})
    return {"review_state": case.review.model_dump(mode="json"), "package": package,
            "note": "The return package has been prepared. ASTRA Tax does not submit anything to the e-filing portal – filing is a separate, explicit step outside this prototype."}


@router.get("/package")
def download_package(case: TaxCase = Depends(load_case)):
    if not case.review.confirmed or not case.review.return_package_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No confirmed return package yet")
    data = get_storage().get(f"{case.id}/packages/{case.review.return_package_id}.json")
    return Response(content=data, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="astra-return-{case.assessment_year}.json"'})


@router.post("/reset")
def reset_review(case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    case.review.confirmed = False
    case.review.confirmed_at = None
    case.review.declarations = {}
    repo.save(case)
    audit.record(repo, case.id, user.id, audit.ACTOR_USER, "review.reopened", "Final review reopened for changes")
    return {"review_state": case.review.model_dump(mode="json")}
