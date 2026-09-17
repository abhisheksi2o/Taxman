"""Deterministic tax computation, regime comparison, line explanations, evidence graph, rules."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import load_case
from app.evidence.graph import entity_evidence, income_evidence_tree, number_explanation
from app.models.tax_model import TaxCase
from app.reconciliation.impact import working_regime
from app.tax_engine.engine import compute
from app.tax_engine.rules import get_rules, supported_years

router = APIRouter(tags=["computation"])


@router.get("/cases/{case_id}/computation")
def computation(as_of: str | None = None, case: TaxCase = Depends(load_case)):
    try:
        comp = compute(case, as_of=date.fromisoformat(as_of) if as_of else None)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {**comp.model_dump(mode="json"), "working_regime": working_regime(case), "open_items": len(case.open_items())}


@router.get("/cases/{case_id}/computation/explain/{regime}/{line_id:path}")
def explain(regime: str, line_id: str, case: TaxCase = Depends(load_case)):
    regime = regime.upper()
    if regime not in ("OLD", "NEW"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "regime must be OLD or NEW")
    comp = compute(case)
    exp = number_explanation(case, comp, regime, line_id)
    if exp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Line not found")
    return exp


@router.get("/cases/{case_id}/evidence/tree")
def evidence_tree(regime: str | None = None, case: TaxCase = Depends(load_case)):
    comp = compute(case)
    r = (regime or working_regime(case)).upper()
    return {"regime": r, "tree": income_evidence_tree(case, comp, r)}


@router.get("/cases/{case_id}/evidence/{entity_id}")
def evidence(entity_id: str, case: TaxCase = Depends(load_case)):
    ev = entity_evidence(case, entity_id)
    if ev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entity not found")
    return ev


@router.get("/rules")
def rules():
    return {"years": supported_years(), "rules": {ay: get_rules(ay).public_summary() for ay in supported_years()}}


@router.get("/rules/{assessment_year}")
def rules_for_year(assessment_year: str):
    try:
        return get_rules(assessment_year).public_summary()
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
