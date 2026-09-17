"""Public API of the demo data generator."""
from __future__ import annotations

import copy

from app.documents.pipeline import RawDocument, ingest
from app.models.common import money
from app.models.tax_model import TaxCase
from app.reconciliation import impact as imp
from app.tax_engine.rules import CURRENT_ASSESSMENT_YEAR

from .scenarios import SCENARIO_META, SCENARIOS, Ctx, GeneratedCase, HiddenIssue


def list_scenarios() -> list[dict]:
    return [{"code": code, "label": meta[0], "description": meta[1]} for code, meta in SCENARIO_META.items()]


def generate(scenario: str, seed: int = 42, assessment_year: str = CURRENT_ASSESSMENT_YEAR) -> GeneratedCase:
    if scenario not in SCENARIOS:
        raise KeyError(f"Unknown scenario '{scenario}'. Available: {sorted(SCENARIOS)}")
    ctx = Ctx(seed, assessment_year)
    return SCENARIOS[scenario](ctx)


def materialize(gen: GeneratedCase, owner_user_id: str, store=None) -> TaxCase:
    """Build the taxpayer's declared case exactly the way the product does: profile from onboarding, entries
    the taxpayer typed in, then every generated document run through the real extraction pipeline."""
    case = TaxCase(owner_user_id=owner_user_id, label=f"{gen.profile.name} · AY {gen.assessment_year}", taxpayer=gen.profile)
    case.meta.demo_scenario = gen.scenario
    case.meta.demo_seed = gen.seed
    case.bank_accounts = [b.model_copy() for b in gen.bank_accounts]
    for entry in gen.user_entries:
        entry(case)
    for d in gen.documents:
        raw = RawDocument(filename=d.filename, mime_type=d.mime_type, data=d.data)
        key = f"{case.id}/{d.filename}".replace(" ", "_")
        doc = ingest(case, raw, hint=None, storage_key=key, allow_llm=False)
        if store is not None:
            store.put(doc.storage_key, d.data)
    return case


def compute_expected_impacts(case: TaxCase, issues: list[HiddenIssue]) -> None:
    for h in issues:
        if h.impact_mode == "TAX_DELTA" and h.fix is not None:
            h.expected_impact = float(imp.tax_delta(case, h.fix))
        elif h.impact_mode == "DUPLICATE":
            # the manual duplicate entry is already counted – removing it reduces tax
            dup = next((i for i in case.income.interest if i.status.value == "USER_ENTERED" and float(i.amount.amount) == h.amount), None)
            h.expected_impact = float(imp.tax_delta(case, imp.remove_entity(dup.id))) if dup else 0.0
        elif h.impact_mode == "CREDIT_DIFFERENCE":
            h.expected_impact = h.expected_impact if h.expected_impact is not None else h.amount
        else:
            h.expected_impact = 0.0


def hidden_test_payload(gen: GeneratedCase) -> dict:
    return {
        "scenario": gen.scenario, "label": gen.label, "description": gen.description, "seed": gen.seed, "assessment_year": gen.assessment_year,
        "story": gen.story, "questions": gen.questions, "hidden_issues": [h.public() for h in gen.hidden_issues],
        "documents": [{"filename": d.filename, "type": d.doc_type.value, "description": d.description} for d in gen.documents],
    }


def snapshot(case: TaxCase) -> TaxCase:
    return copy.deepcopy(case)
