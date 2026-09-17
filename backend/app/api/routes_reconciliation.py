"""Reconciliation items, alerts and their resolution."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import current_user, get_repo, load_case
from app.core import audit
from app.db.models import User
from app.db.repository import CaseRepository
from app.models.tax_model import TaxCase
from app.reconciliation.engine import reconcile
from app.services import case_ops
from app.services import reconciliation_service as svc
from app.services.errors import ServiceError
from app.services.views import case_view

router = APIRouter(prefix="/cases/{case_id}", tags=["reconciliation"])
PRIORITY = svc.PRIORITY


class Resolution(BaseModel):
    action: str  # ADD_INCOME | REMOVE_DUPLICATE | CONFIRM_ENTITY | RECLASSIFY | REVIEW | DISMISS
    payload: dict[str, Any] | None = None
    note: str | None = None


@router.get("/reconciliation")
def get_reconciliation(case: TaxCase = Depends(load_case)):
    return svc.reconciliation_view(case)


@router.post("/reconciliation/run")
def run_reconciliation(case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    items = reconcile(case)
    repo.save(case)
    audit.record(repo, case.id, user.id, audit.ACTOR_ASTRA, "reconciliation.run", f"Reconciliation run – {len([i for i in items if i.status == 'OPEN'])} open item(s)")
    return {"items": [i.model_dump(mode="json") for i in items], "matrix": svc.matrix(case)}


@router.get("/issues")
def issues(case: TaxCase = Depends(load_case)):
    return svc.issues_view(case)


@router.get("/reconciliation/{item_id}")
def get_item(item_id: str, case: TaxCase = Depends(load_case)):
    try:
        return svc.item_detail(case, item_id)
    except ServiceError as exc:
        raise HTTPException(exc.status, exc.detail) from exc


@router.post("/reconciliation/{item_id}/resolve")
def resolve_item(item_id: str, body: Resolution, case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    try:
        item, summary, navigate = svc.resolve_item(case, item_id, body.action, body.payload, body.note)
    except ServiceError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    if navigate:
        return {"navigate": navigate, "case": case_view(case)}
    case_ops.commit(repo, case, user.id, audit.ACTOR_USER, "reconciliation.resolved", f"{summary}: {item.title}", {"item_id": item.id, "action": body.action.upper(), "note": body.note},
                    [{"label": e.label, "document_id": e.document_id} for e in item.evidence[:4]])
    current = next((i for i in case.reconciliation_items if i.id == item_id), item)
    return {"item": current.model_dump(mode="json"), "case": case_view(case)}


@router.post("/reconciliation/{item_id}/reopen")
def reopen_item(item_id: str, case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    try:
        item = svc.reopen_item(case, item_id)
    except ServiceError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    case_ops.commit(repo, case, user.id, audit.ACTOR_USER, "reconciliation.reopened", f"Item reopened: {item.title}", {"item_id": item.id})
    return {"item": item.model_dump(mode="json")}
