"""Dashboard aggregate – one call for the landing page."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_repo, load_case
from app.db.repository import CaseRepository
from app.models.tax_model import TaxCase
from app.services.dashboard_service import dashboard as build_dashboard

router = APIRouter(prefix="/cases/{case_id}/dashboard", tags=["dashboard"])


@router.get("")
def dashboard(case: TaxCase = Depends(load_case), repo: CaseRepository = Depends(get_repo)):
    return build_dashboard(case, repo.audit_trail(case.id, limit=8))
