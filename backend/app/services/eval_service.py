"""Hidden-test comparison for a demo case (developer evaluation screen)."""
from __future__ import annotations

from app.evaluation.runner import item_matches
from app.models.tax_model import TaxCase
from app.synthetic.scenarios import HiddenIssue


def hidden_test_rows(case: TaxCase, hidden: dict) -> dict:
    rows = []
    for h in hidden.get("hidden_issues", []):
        hi = HiddenIssue(**{k: v for k, v in h.items() if k != "fix"})
        hit = next((i for i in case.reconciliation_items if item_matches(hi, i)), None)
        rows.append({**h, "detected": hit is not None, "detected_item": ({"id": hit.id, "title": hit.title, "status": hit.status, "impact": float(hit.impact_estimate) if hit.impact_estimate is not None else None,
                                                                          "evidence": [e.label for e in hit.evidence]} if hit else None)})
    return {**{k: v for k, v in hidden.items() if k != "hidden_issues"}, "hidden_issues": rows}
