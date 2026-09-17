"""Synthetic demo data generator endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import current_user, get_repo
from app.api.serializers import case_view
from app.config import Settings, get_settings
from app.core import audit
from app.core.storage import get_storage
from app.db.models import User
from app.db.repository import CaseRepository
from app.models.common import SOURCE_LABELS
from app.reconciliation.engine import reconcile
from app.synthetic.generator import compute_expected_impacts, generate, hidden_test_payload, list_scenarios, materialize
from app.tax_engine.engine import compute
from app.tax_engine.rules import CURRENT_ASSESSMENT_YEAR, get_rules

router = APIRouter(prefix="/demo", tags=["demo"])


class GenerateRequest(BaseModel):
    scenario: str
    seed: int = Field(default=42, ge=0, le=10_000_000)
    assessment_year: str = CURRENT_ASSESSMENT_YEAR


@router.get("/scenarios")
def scenarios(settings: Settings = Depends(get_settings)):
    return {"enabled": settings.astra_demo_mode, "scenarios": list_scenarios()}


@router.post("/generate", status_code=201)
def generate_demo(body: GenerateRequest, user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo), settings: Settings = Depends(get_settings)):
    if not settings.astra_demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Demo mode is disabled")
    try:
        get_rules(body.assessment_year)
        gen = generate(body.scenario, body.seed, body.assessment_year)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    case = materialize(gen, owner_user_id=user.id, store=get_storage())
    compute_expected_impacts(case, gen.hidden_issues)
    items = reconcile(case)
    computation = compute(case)
    repo.create(case, hidden_test=hidden_test_payload(gen))
    # replay a realistic audit timeline
    audit.record(repo, case.id, user.id, audit.ACTOR_USER, "demo.generated", f"Demo taxpayer generated – scenario '{gen.label}' (seed {gen.seed})", {"scenario": gen.scenario, "seed": gen.seed})
    audit.record(repo, case.id, user.id, audit.ACTOR_USER, "profile.updated", "Onboarding completed", {"fields": ["name", "pan", "date_of_birth", "income_sources"]})
    for d in case.documents:
        audit.record(repo, case.id, user.id, audit.ACTOR_USER, "document.uploaded", f"{SOURCE_LABELS.get(d.type, d.type.value)} uploaded ({d.filename})", {"document_id": d.id})
        audit.record(repo, case.id, None, audit.ACTOR_ASTRA, "document.extracted", f"{SOURCE_LABELS.get(d.type, d.type.value)} processed – {len(d.linked_entity_ids)} value(s) extracted at {int(d.extraction_confidence * 100)}% confidence",
                     {"document_id": d.id, "method": d.extraction_method, "confidence": d.extraction_confidence, "flags": d.flags}, [{"label": d.filename, "document_id": d.id}])
    audit.record(repo, case.id, None, audit.ACTOR_ASTRA, "reconciliation.run", f"Reconciliation run – {len([i for i in items if i.status == 'OPEN'])} open item(s)", {"open_items": len([i for i in items if i.status == 'OPEN'])})
    audit.record(repo, case.id, None, audit.ACTOR_SYSTEM, "computation.updated", "Tax calculation updated", {"rules_version": computation.rules_version})
    return {"case": case_view(case), "scenario": {"code": gen.scenario, "label": gen.label, "description": gen.description, "story": gen.story},
            "open_items": len([i for i in items if i.status == "OPEN"])}
