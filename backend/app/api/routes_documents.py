"""Document upload, listing, source viewing and deletion."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from app.api.deps import current_user, get_repo, load_case
from app.api.serializers import case_view
from app.config import Settings, get_settings
from app.core import audit
from app.core.security import upload_limiter
from app.core.storage import get_storage
from app.db.models import User
from app.db.repository import CaseRepository
from app.documents.pipeline import DocumentError, RawDocument, ingest
from app.models.common import SOURCE_LABELS, SourceType
from app.models.tax_model import TaxCase
from app.services import case_ops

router = APIRouter(prefix="/cases/{case_id}/documents", tags=["documents"])


def _doc_view(d) -> dict:
    v = d.model_dump(mode="json")
    v.pop("storage_key", None)
    v["type_label"] = SOURCE_LABELS.get(d.type, d.type.value)
    return v


@router.get("")
def list_documents(case: TaxCase = Depends(load_case)):
    return {"documents": [_doc_view(d) for d in case.documents], "types": [{"code": s.value, "label": l} for s, l in SOURCE_LABELS.items() if s not in (SourceType.USER_INPUT, SourceType.CALCULATION, SourceType.AI_EXTRACTION)]}


@router.post("", status_code=201)
async def upload_document(request: Request, file: UploadFile = File(...), document_type: str | None = Form(default=None), case: TaxCase = Depends(load_case),
                          user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo), settings: Settings = Depends(get_settings)):
    if not upload_limiter.allow(f"upload:{user.id}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many uploads – slow down")
    data = await file.read()
    if len(data) > settings.astra_max_upload_mb * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"File exceeds {settings.astra_max_upload_mb} MB")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    hint = None
    if document_type:
        try:
            hint = SourceType(document_type)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown document type") from exc
    raw = RawDocument(filename=file.filename or "document", mime_type=file.content_type or "", data=data)
    try:
        doc = ingest(case, raw, hint=hint)
    except DocumentError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    get_storage().put(doc.storage_key, data)
    audit.record(repo, case.id, user.id, audit.ACTOR_USER, "document.uploaded", f"{SOURCE_LABELS.get(doc.type, doc.type.value)} uploaded ({doc.filename})", {"document_id": doc.id, "size": doc.size_bytes})
    audit.record(repo, case.id, None, audit.ACTOR_ASTRA, "document.extracted",
                 f"{SOURCE_LABELS.get(doc.type, doc.type.value)} processed – {len(doc.linked_entity_ids)} value(s) extracted at {int(doc.extraction_confidence * 100)}% confidence",
                 {"document_id": doc.id, "method": doc.extraction_method, "confidence": doc.extraction_confidence, "flags": doc.flags},
                 [{"label": f"{doc.filename}", "document_id": doc.id}])
    case_ops.commit(repo, case, user.id, audit.ACTOR_SYSTEM, "document.attached", f"Model updated from {doc.filename}", {"document_id": doc.id, "entities": doc.linked_entity_ids})
    return {"document": _doc_view(doc), "case": case_view(case)}


@router.get("/{document_id}")
def get_document(document_id: str, case: TaxCase = Depends(load_case)):
    doc = case.document(document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    linked = [case.find_entity(e) for e in doc.linked_entity_ids]
    return {"document": _doc_view(doc), "linked_entities": [e.model_dump(mode="json") for e in linked if e is not None],
            "related_items": [i.model_dump(mode="json") for i in case.reconciliation_items if any(ev.document_id == document_id for ev in i.evidence)]}


@router.post("/{document_id}/reprocess")
def reprocess(document_id: str, document_type: str | None = None, case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    """Re-run extraction (e.g. after choosing the correct document type)."""
    doc = case.document(document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    data = get_storage().get(doc.storage_key)
    hint = SourceType(document_type) if document_type else doc.type
    for eid in doc.linked_entity_ids:
        case_ops.remove_entity(case, eid)
    case.documents = [d for d in case.documents if d.id != document_id]
    raw = RawDocument(filename=doc.filename, mime_type=doc.mime_type, data=data)
    try:
        new_doc = ingest(case, raw, hint=hint, storage_key=doc.storage_key)
    except DocumentError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    case_ops.commit(repo, case, user.id, audit.ACTOR_ASTRA, "document.reprocessed", f"{new_doc.filename} re-processed as {SOURCE_LABELS.get(new_doc.type, new_doc.type.value)}", {"document_id": new_doc.id})
    return {"document": _doc_view(new_doc), "case": case_view(case)}


@router.delete("/{document_id}")
def delete_document(document_id: str, case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    doc = case.document(document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    for eid in doc.linked_entity_ids:
        case_ops.remove_entity(case, eid)
    for t in list(case.tax_deducted):
        if document_id in t.document_ids and len(t.document_ids) == 1:
            case.tax_deducted.remove(t)
    case.documents = [d for d in case.documents if d.id != document_id]
    try:
        get_storage().delete(doc.storage_key)
    except Exception:  # noqa: BLE001
        pass
    case_ops.commit(repo, case, user.id, audit.ACTOR_USER, "document.deleted", f"{doc.filename} deleted (and its extracted values removed)", {"document_id": document_id})
    return {"case": case_view(case)}
