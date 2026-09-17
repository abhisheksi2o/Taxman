"""Audit trail."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_repo, load_case
from app.db.repository import CaseRepository
from app.models.tax_model import TaxCase

router = APIRouter(prefix="/cases/{case_id}/audit", tags=["audit"])


@router.get("")
def audit_trail(limit: int = 200, case: TaxCase = Depends(load_case), repo: CaseRepository = Depends(get_repo)):
    return {"events": repo.audit_trail(case.id, limit=min(limit, 500))}
