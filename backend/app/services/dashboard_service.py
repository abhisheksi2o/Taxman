"""Dashboard aggregate shared by the HTTP API and the in-browser backend."""
from __future__ import annotations

from app.models.tax_model import TaxCase
from app.readiness.checker import check
from app.readiness.requirements import requirements_for
from app.reconciliation.impact import working_regime
from app.services.reconciliation_service import PRIORITY
from app.tax_engine.engine import compute


def dashboard(case: TaxCase, recent_activity: list[dict]) -> dict:
    comp = compute(case)
    regime = working_regime(case)
    rc = comp.regime(regime)
    report = check(case, comp)
    open_items = case.open_items()
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
    alerts = [{"id": i.id, "severity": i.severity, "priority": PRIORITY[i.severity], "title": i.title, "kind": i.kind, "category": i.category,
               "impact": float(i.impact_estimate) if i.impact_estimate is not None else None, "required_action": i.required_action}
              for i in sorted(open_items, key=lambda x: order.get(x.severity, 9))[:6]]
    s = rc.summary
    composition = [{"label": lbl, "amount": float(getattr(s, key))} for key, lbl in (("income_salary", "Salary"), ("income_house_property", "House property"), ("income_capital_gains", "Capital gains"),
                                                                                    ("income_business", "Business / profession"), ("income_other_sources", "Other sources")) if float(getattr(s, key)) != 0]
    reqs = requirements_for(case)
    return {
        "case": {"id": case.id, "label": case.label, "assessment_year": case.assessment_year, "taxpayer_name": case.taxpayer.name, "onboarding_completed": case.taxpayer.onboarding_completed,
                 "demo_scenario": case.meta.demo_scenario, "updated_at": case.meta.updated_at.isoformat()},
        "regime": regime, "rules_version": comp.rules_version, "readiness": report.model_dump(mode="json"),
        "kpis": {"gross_total_income": float(s.gross_total_income), "taxable_income": float(s.taxable_income), "total_tax": float(s.total_tax_liability), "credits": float(s.total_credits),
                 "net_payable": float(s.net_payable), "position": ("refund" if s.net_payable < 0 else "payable" if s.net_payable > 0 else "nil"), "effective_rate": float(s.effective_tax_rate)},
        "comparison": {"lower": comp.comparison.lower_tax_regime, "difference": float(comp.comparison.difference), "old_total": float(comp.old.summary.total_tax_liability), "new_total": float(comp.new.summary.total_tax_liability)},
        "alerts": {"headline": f"ASTRA FOUND {len(open_items)} ITEM{'S' if len(open_items) != 1 else ''} TO REVIEW", "count": len(open_items), "items": alerts},
        "composition": composition,
        "documents": {"count": len(case.documents), "needs_review": len([d for d in case.documents if d.status in ("NEEDS_REVIEW", "FAILED")]),
                      "required_missing": [r.label for r in reqs if r.kind == "DOCUMENT" and r.priority == "REQUIRED" and r.status == "MISSING"]},
        "recent_activity": recent_activity,
    }
