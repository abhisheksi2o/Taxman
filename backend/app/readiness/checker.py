"""Before-You-File verification engine – seven deterministic checks and a readiness score."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.common import ValueStatus, money
from app.models.tax_model import TaxCase
from app.tax_engine.engine import fmt
from app.tax_engine.schema import TaxComputation

from .requirements import requirements_for

ZERO = Decimal("0")


class CheckResult(BaseModel):
    code: str
    label: str
    question: str
    status: str  # PASS | WARN | FAIL
    score: float  # 0..1
    weight: float
    findings: list[str] = Field(default_factory=list)
    related_item_ids: list[str] = Field(default_factory=list)
    action: str | None = None


class ReadinessReport(BaseModel):
    score: int
    checks: list[CheckResult]
    issues_requiring_review: int
    information_missing: int
    calculation_warnings: int
    unconfirmed_values: int
    blocking: list[str]
    summary: str


def check(case: TaxCase, computation: TaxComputation) -> ReadinessReport:
    checks: list[CheckResult] = []
    open_items = case.open_items()
    reqs = requirements_for(case)

    # 1. Completeness – are required income sources accounted for?
    missing_income = [i for i in open_items if i.kind == "MISSING" and i.category in ("SALARY", "INTEREST", "DIVIDEND", "CAPITAL_GAINS", "RENTAL", "BUSINESS", "OTHER")]
    missing_info = [r for r in reqs if r.kind == "INFORMATION" and r.priority == "REQUIRED" and r.status == "MISSING"]
    c = CheckResult(code="completeness", label="Completeness", question="Are required income sources accounted for?", status="PASS", score=1.0, weight=0.25)
    if missing_income:
        c.status = "FAIL" if any(i.severity == "HIGH" for i in missing_income) else "WARN"
        c.score = max(0.0, 1 - 0.35 * len(missing_income))
        c.findings += [i.title for i in missing_income]
        c.related_item_ids += [i.id for i in missing_income]
        c.action = "Review the missing-income items and confirm or add them."
    if missing_info:
        c.status = "FAIL" if c.status == "FAIL" else "WARN"
        c.score = min(c.score, 0.7)
        c.findings += [f"{r.label} not provided" for r in missing_info]
    checks.append(c)

    # 2. Consistency – do major numbers reconcile?
    mismatches = [i for i in open_items if i.kind in ("MISMATCH", "DUPLICATE", "TIMING") and i.category != "PREVIOUS_YEAR" and i.severity != "INFO"]
    c = CheckResult(code="consistency", label="Consistency", question="Do major numbers reconcile across sources?", status="PASS", score=1.0, weight=0.2)
    if mismatches:
        c.status = "FAIL" if any(i.severity == "HIGH" for i in mismatches) else "WARN"
        c.score = max(0.0, 1 - 0.3 * len(mismatches))
        c.findings += [i.title for i in mismatches]
        c.related_item_ids += [i.id for i in mismatches]
        c.action = "Open Reconciliation and resolve each mismatch."
    if not case.documents:
        c.status, c.score = "WARN", 0.4
        c.findings.append("No documents uploaded – nothing to reconcile against.")
    checks.append(c)

    # 3. Documents – are required supporting documents available?
    required_docs = [r for r in reqs if r.kind == "DOCUMENT" and r.priority == "REQUIRED"]
    missing_docs = [r for r in required_docs if r.status == "MISSING"]
    recommended_missing = [r for r in reqs if r.kind == "DOCUMENT" and r.priority == "RECOMMENDED" and r.status == "MISSING"]
    c = CheckResult(code="documents", label="Documents", question="Are required supporting documents available?", status="PASS", score=1.0, weight=0.15)
    if missing_docs:
        c.status = "FAIL" if any(r.code in ("doc.form16", "doc.26as") for r in missing_docs) else "WARN"
        c.score = max(0.0, 1 - len(missing_docs) / max(1, len(required_docs)))
        c.findings += [f"{r.label} missing" for r in missing_docs]
        c.action = "Upload the missing documents from the Documents page."
    if recommended_missing:
        c.findings += [f"{r.label} recommended" for r in recommended_missing]
        c.score = min(c.score, 0.9)
        if c.status == "PASS":
            c.status = "WARN" if len(recommended_missing) > 1 else "PASS"
    needs_review = [d for d in case.documents if d.status in ("NEEDS_REVIEW", "FAILED")]
    if needs_review:
        c.findings += [f"{d.filename}: {', '.join(d.flags) or d.status.lower()}" for d in needs_review]
        c.score = min(c.score, 0.85)
        c.status = "WARN" if c.status == "PASS" else c.status
    checks.append(c)

    # 4. Calculations – internally consistent?
    warnings = list(computation.validation_warnings) + [w for w in computation.new.warnings + computation.old.warnings]
    c = CheckResult(code="calculations", label="Calculations", question="Are tax calculations internally consistent?", status="PASS", score=1.0, weight=0.1)
    if warnings:
        c.status = "WARN"
        c.score = max(0.4, 1 - 0.15 * len(warnings))
        c.findings += list(dict.fromkeys(warnings))
        c.action = "Review the assumptions listed under Tax Computation."
    if computation.new.summary.gross_total_income <= 0 and computation.old.summary.gross_total_income <= 0:
        c.status, c.score = "FAIL", 0.0
        c.findings.append("Gross total income is zero – no income has been recorded yet.")
    checks.append(c)

    # 5. TDS – does TDS reconcile?
    tds_items = [i for i in open_items if i.category == "TDS"]
    conflicts = [l for l in computation.new.credit_ledger if l.get("conflict")]
    provisional = [l for l in computation.new.credit_ledger if l.get("provisional")]
    c = CheckResult(code="tds", label="TDS", question="Does available TDS information reconcile?", status="PASS", score=1.0, weight=0.1)
    if tds_items or conflicts:
        c.status = "FAIL" if any(i.severity == "HIGH" for i in tds_items) else "WARN"
        c.score = max(0.0, 1 - 0.4 * max(len(tds_items), len(conflicts)))
        c.findings += [i.title for i in tds_items] + [f"{l['deductor']}: sources differ, using {l['used_source']}" for l in conflicts if not any(l['deductor'].lower() in i.title.lower() for i in tds_items)]
        c.related_item_ids += [i.id for i in tds_items]
        c.action = "Confirm the 26AS figure or follow up with the deductor."
    if provisional:
        c.status = "WARN" if c.status == "PASS" else c.status
        c.score = min(c.score, 0.8)
        c.findings += [f"{l['deductor']}: TDS credit not yet visible in 26AS / AIS" for l in provisional]
    checks.append(c)

    # 6. Deductions – supported by information?
    c = CheckResult(code="deductions", label="Deductions", question="Are deduction claims supported by available information?", status="PASS", score=1.0, weight=0.1)
    unsupported = [d for d in case.deductions if not d.supporting_document_ids and d.status not in (ValueStatus.USER_CONFIRMED,)]
    unsupported_inv = [i for i in case.investments if not i.document_ids and i.status not in (ValueStatus.USER_CONFIRMED, ValueStatus.EXTRACTED)]
    restricted = [l for top in computation.old.lines if top.id == "via" for l in top.children if l.notes and any("restricted" in n.lower() for n in l.notes)]
    if unsupported or unsupported_inv:
        c.status = "WARN"
        c.score = 0.7
        c.findings += [f"{d.description} ({d.section}) – no supporting document" for d in unsupported] + [f"{i.instrument.replace('_', ' ').title()} – no proof attached" for i in unsupported_inv]
        c.action = "Attach proofs or confirm the claims."
    if restricted:
        c.findings += [f"{l.label}: {' '.join(l.notes)}" for l in restricted]
        c.status = "WARN" if c.status == "PASS" else c.status
        c.score = min(c.score, 0.85)
    checks.append(c)

    # 7. Profile – taxpayer details complete?
    tp = case.taxpayer
    c = CheckResult(code="profile", label="Profile", question="Are taxpayer details complete?", status="PASS", score=1.0, weight=0.1)
    gaps = []
    if not tp.name:
        gaps.append("name")
    if not tp.pan:
        gaps.append("PAN")
    if not tp.date_of_birth:
        gaps.append("date of birth")
    if not case.bank_accounts:
        gaps.append("bank account for refund")
    if not tp.onboarding_completed:
        gaps.append("onboarding not completed")
    if gaps:
        c.status = "FAIL" if ("PAN" in gaps or "name" in gaps) else "WARN"
        c.score = max(0.0, 1 - 0.25 * len(gaps))
        c.findings += [f"Missing: {g}" for g in gaps]
        c.action = "Complete My Tax Profile."
    checks.append(c)

    total_w = sum(ch.weight for ch in checks)
    score = int(round(100 * sum(ch.score * ch.weight for ch in checks) / total_w))
    unconfirmed = sum(1 for e in case.income.all_entities() if getattr(e, "status", None) in (ValueStatus.EXTRACTED, ValueStatus.AI_SUGGESTED))
    blocking = [ch.label for ch in checks if ch.status == "FAIL"]
    review_count = len([i for i in open_items if i.severity in ("HIGH", "MEDIUM", "LOW")])
    missing_count = len([r for r in reqs if r.priority == "REQUIRED" and r.status == "MISSING"])
    summary = (f"{review_count} item{'s' if review_count != 1 else ''} to review, {missing_count} required item{'s' if missing_count != 1 else ''} missing, "
               f"{len(warnings)} calculation warning{'s' if len(warnings) != 1 else ''}; {unconfirmed} extracted value{'s' if unconfirmed != 1 else ''} still await your confirmation.")
    return ReadinessReport(score=score, checks=checks, issues_requiring_review=review_count, information_missing=missing_count, calculation_warnings=len(warnings),
                           unconfirmed_values=unconfirmed, blocking=blocking, summary=summary)
