"""Synthetic demo data generator endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import current_user, get_repo
from app.config import Settings, get_settings
from app.core.storage import get_storage
from app.db.models import User
from app.db.repository import CaseRepository
from app.services import demo_service as svc
from app.services.errors import ServiceError
from app.services.views import case_view
from app.synthetic.generator import list_scenarios
from app.tax_engine.rules import CURRENT_ASSESSMENT_YEAR

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
        case, gen, items, computation, hidden = svc.generate_demo(body.scenario, body.seed, body.assessment_year, user.id, get_storage())
    except ServiceError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    repo.create(case, hidden_test=hidden)
    svc.record_demo_timeline(repo, case, gen, items, computation, user.id)
    return {"case": case_view(case), "scenario": {"code": gen.scenario, "label": gen.label, "description": gen.description, "story": gen.story},
            "open_items": len([i for i in items if i.status == "OPEN"])}
