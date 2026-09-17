"""Before-You-File check and the final return review with explicit confirmation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.deps import current_user, get_repo, load_case
from app.core import audit
from app.core.storage import get_storage
from app.db.models import User
from app.db.repository import CaseRepository
from app.models.tax_model import TaxCase
from app.services import review_service as svc
from app.services.errors import ServiceError

router = APIRouter(prefix="/cases/{case_id}/review", tags=["review"])
DECLARATIONS = svc.DECLARATIONS


class Confirmation(BaseModel):
    declarations: dict[str, bool]
    selected_regime: str
    acknowledge_open_issues: bool = False


@router.get("")
def review(case: TaxCase = Depends(load_case)):
    return svc.review_view(case)


@router.post("/confirm")
def confirm(body: Confirmation, case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    try:
        package, high, score = svc.confirm_review(case, body.declarations, body.selected_regime, body.acknowledge_open_issues, get_storage())
    except ServiceError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    repo.save(case)
    audit.record(repo, case.id, user.id, audit.ACTOR_USER, "review.confirmed", f"User confirmed the final review ({case.review.selected_regime.lower()} regime, {high} high-priority item(s) acknowledged)",
                 {"package_id": case.review.return_package_id, "regime": case.review.selected_regime, "readiness": score})
    return {"review_state": case.review.model_dump(mode="json"), "package": package, "note": svc.CONFIRM_NOTE}


@router.get("/package")
def download_package(case: TaxCase = Depends(load_case)):
    if not case.review.confirmed or not case.review.return_package_id:
        raise HTTPException(404, "No confirmed return package yet")
    data = get_storage().get(svc.package_key(case))
    return Response(content=data, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="astra-return-{case.assessment_year}.json"'})


@router.post("/reset")
def reset_review(case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    svc.reset_review(case)
    repo.save(case)
    audit.record(repo, case.id, user.id, audit.ACTOR_USER, "review.reopened", "Final review reopened for changes")
    return {"review_state": case.review.model_dump(mode="json")}
