"""Cases (one per taxpayer per AY): CRUD, onboarding, profile, entities."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import current_user, get_repo, load_case
from app.api.serializers import case_view, profile_view
from app.core import audit
from app.core.storage import get_storage
from app.db.models import User
from app.db.repository import CaseRepository
from app.models.common import ValueStatus
from app.models.tax_model import TaxCase
from app.readiness.requirements import next_onboarding_step, requirements_for
from app.services import case_ops
from app.tax_engine.rules import CURRENT_ASSESSMENT_YEAR, get_rules, supported_years

router = APIRouter(prefix="/cases", tags=["cases"])


class NewCase(BaseModel):
    assessment_year: str = CURRENT_ASSESSMENT_YEAR
    label: str | None = Field(default=None, max_length=120)


class ProfilePatch(BaseModel):
    values: dict[str, Any]
    confirmed: bool = False


class EntityIn(BaseModel):
    head: str
    payload: dict[str, Any]
    confirmed: bool = True


class EntityPatch(BaseModel):
    changes: dict[str, Any]


@router.get("")
def list_cases(user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    return {"cases": repo.list_for_user(user.id), "supported_years": supported_years(), "current_year": CURRENT_ASSESSMENT_YEAR}


@router.post("", status_code=201)
def create_case(body: NewCase, user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    try:
        get_rules(body.assessment_year)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    case = TaxCase(owner_user_id=user.id, label=body.label or f"Tax return · AY {body.assessment_year}")
    case.taxpayer.assessment_year = body.assessment_year
    repo.create(case)
    audit.record(repo, case.id, user.id, audit.ACTOR_USER, "case.created", f"Return workspace created for AY {body.assessment_year}")
    return case_view(case)


@router.get("/{case_id}")
def get_case(case: TaxCase = Depends(load_case)):
    return case_view(case)


@router.delete("/{case_id}")
def delete_case(case_id: str, user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    keys = repo.delete(case_id, user.id)
    store = get_storage()
    for k in keys:
        try:
            store.delete(k)
        except Exception:  # noqa: BLE001
            pass
    return {"deleted": True, "documents_purged": len(keys)}


# ---- onboarding / profile -------------------------------------------------------------------

@router.get("/{case_id}/onboarding")
def onboarding(case: TaxCase = Depends(load_case)):
    return {**next_onboarding_step(case), "profile": profile_view(case)}


ALLOWED_PROFILE_FIELDS = {"assessment_year", "name", "pan", "date_of_birth", "taxpayer_type", "residential_status", "employment_status", "income_sources", "employer_count",
                          "has_investments", "has_rental_income", "property_count", "has_home_loan", "has_foreign_income_or_assets", "filed_previous_return", "city_type",
                          "regime_preference", "email", "phone", "address", "onboarding_completed"}


@router.patch("/{case_id}/profile")
def patch_profile(body: ProfilePatch, case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    values = {k: v for k, v in body.values.items() if k in ALLOWED_PROFILE_FIELDS}
    if "assessment_year" in values:
        try:
            get_rules(values["assessment_year"])
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if "pan" in values and values["pan"]:
        pan = str(values["pan"]).strip().upper()
        if len(pan) != 10 or not (pan[:5].isalpha() and pan[5:9].isdigit() and pan[9].isalpha()):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "PAN must look like ABCDE1234F")
        values["pan"] = pan
    try:
        updated = case.taxpayer.model_copy(update=values)
        case.taxpayer = type(case.taxpayer).model_validate(updated.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid profile values: {exc}") from exc
    for k in values:
        case.taxpayer.field_status[k] = ValueStatus.USER_CONFIRMED if body.confirmed else ValueStatus.USER_ENTERED
    step = next_onboarding_step(case)
    if step["completed"]:
        case.taxpayer.onboarding_completed = True
    case_ops.commit(repo, case, user.id, audit.ACTOR_USER, "profile.updated", f"Profile updated ({', '.join(sorted(values))})", {"fields": sorted(values)})
    return {**next_onboarding_step(case), "profile": profile_view(case)}


@router.get("/{case_id}/requirements")
def requirements(case: TaxCase = Depends(load_case)):
    return {"items": [r.as_dict() for r in requirements_for(case)]}


# ---- entities -----------------------------------------------------------------------------------

@router.post("/{case_id}/entities", status_code=201)
def add_entity(body: EntityIn, case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    head = body.head.upper()
    if head not in case_ops.HEADS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown head {head}")
    try:
        entity = case_ops.build_entity(head, body.payload, ValueStatus.USER_CONFIRMED if body.confirmed else ValueStatus.USER_ENTERED)
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid payload: {exc}") from exc
    case_ops.collection_for(case, head).append(entity)
    if head in ("SALARY", "INTEREST", "DIVIDEND", "RENTAL", "CAPITAL_GAINS", "BUSINESS", "OTHER") and head not in case.taxpayer.income_sources:
        mapping = {"RENTAL": "RENTAL", "CAPITAL_GAINS": "CAPITAL_GAINS", "BUSINESS": "BUSINESS", "OTHER": "OTHER", "SALARY": "SALARY", "INTEREST": "INTEREST", "DIVIDEND": "DIVIDEND"}
        case.taxpayer.income_sources.append(mapping[head])
    case_ops.commit(repo, case, user.id, audit.ACTOR_USER, "entity.added", f"{head.replace('_', ' ').title()} entry added by taxpayer", {"head": head, "entity_id": entity.id})
    return {"entity": entity.model_dump(mode="json"), "case": case_view(case)}


@router.patch("/{case_id}/entities/{entity_id}")
def patch_entity(entity_id: str, body: EntityPatch, case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    if not case_ops.update_entity(case, entity_id, body.changes):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entity not found")
    case_ops.commit(repo, case, user.id, audit.ACTOR_USER, "entity.updated", "Entry edited and confirmed by taxpayer", {"entity_id": entity_id, "fields": sorted(body.changes)})
    return {"entity": case.find_entity(entity_id).model_dump(mode="json"), "case": case_view(case)}


@router.post("/{case_id}/entities/{entity_id}/confirm")
def confirm_entity(entity_id: str, case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    if not case_ops.confirm_entity(case, entity_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entity not found")
    case_ops.commit(repo, case, user.id, audit.ACTOR_USER, "entity.confirmed", "User confirmed an extracted value", {"entity_id": entity_id})
    return {"entity": case.find_entity(entity_id).model_dump(mode="json"), "case": case_view(case)}


@router.delete("/{case_id}/entities/{entity_id}")
def delete_entity(entity_id: str, case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    if not case_ops.remove_entity(case, entity_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entity not found")
    case_ops.commit(repo, case, user.id, audit.ACTOR_USER, "entity.removed", "Entry removed by taxpayer", {"entity_id": entity_id})
    return {"case": case_view(case)}
