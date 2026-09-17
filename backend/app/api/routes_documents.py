"""Document upload, listing, source viewing and deletion."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.deps import current_user, get_repo, load_case
from app.config import Settings, get_settings
from app.core import audit
from app.core.security import upload_limiter
from app.core.storage import get_storage
from app.db.models import User
from app.db.repository import CaseRepository
from app.models.common import SOURCE_LABELS
from app.models.tax_model import TaxCase
from app.services import case_ops
from app.services import document_service as svc
from app.services.errors import ServiceError
from app.services.views import case_view, doc_view

router = APIRouter(prefix="/cases/{case_id}/documents", tags=["documents"])


@router.get("")
def list_documents(case: TaxCase = Depends(load_case)):
    return {"documents": [doc_view(d) for d in case.documents], "types": svc.document_types()}


@router.post("", status_code=201)
async def upload_document(file: UploadFile = File(...), document_type: str | None = Form(default=None), case: TaxCase = Depends(load_case),
                          user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo), settings: Settings = Depends(get_settings)):
    if not upload_limiter.allow(f"upload:{user.id}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many uploads – slow down")
    data = await file.read()
    try:
        doc = svc.upload(case, file.filename or "document", file.content_type or "", data, document_type, get_storage(), settings.astra_max_upload_mb)
    except ServiceError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    audit.record(repo, case.id, user.id, audit.ACTOR_USER, "document.uploaded", f"{SOURCE_LABELS.get(doc.type, doc.type.value)} uploaded ({doc.filename})", {"document_id": doc.id, "size": doc.size_bytes})
    audit.record(repo, case.id, None, audit.ACTOR_ASTRA, "document.extracted", svc.extraction_summary(doc),
                 {"document_id": doc.id, "method": doc.extraction_method, "confidence": doc.extraction_confidence, "flags": doc.flags}, [{"label": f"{doc.filename}", "document_id": doc.id}])
    case_ops.commit(repo, case, user.id, audit.ACTOR_SYSTEM, "document.attached", f"Model updated from {doc.filename}", {"document_id": doc.id, "entities": doc.linked_entity_ids})
    return {"document": doc_view(doc), "case": case_view(case)}


@router.get("/{document_id}")
def get_document(document_id: str, case: TaxCase = Depends(load_case)):
    try:
        return svc.document_detail(case, document_id)
    except ServiceError as exc:
        raise HTTPException(exc.status, exc.detail) from exc


@router.post("/{document_id}/reprocess")
def reprocess(document_id: str, document_type: str | None = None, case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    try:
        new_doc = svc.reprocess(case, document_id, document_type, get_storage())
    except ServiceError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    case_ops.commit(repo, case, user.id, audit.ACTOR_ASTRA, "document.reprocessed", f"{new_doc.filename} re-processed as {SOURCE_LABELS.get(new_doc.type, new_doc.type.value)}", {"document_id": new_doc.id})
    return {"document": doc_view(new_doc), "case": case_view(case)}


@router.delete("/{document_id}")
def delete_document(document_id: str, case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    try:
        doc = svc.delete_document(case, document_id, get_storage())
    except ServiceError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    case_ops.commit(repo, case, user.id, audit.ACTOR_USER, "document.deleted", f"{doc.filename} deleted (and its extracted values removed)", {"document_id": document_id})
    return {"case": case_view(case)}
