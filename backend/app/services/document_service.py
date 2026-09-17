"""Document upload / reprocess / delete shared by the HTTP API and the in-browser backend."""
from __future__ import annotations

from app.documents.pipeline import DocumentError, RawDocument, ingest
from app.models.common import SOURCE_LABELS, SourceType
from app.models.tax_model import DocumentRecord, TaxCase
from app.services import case_ops
from app.services.errors import ServiceError
from app.services.views import doc_view


def document_types() -> list[dict]:
    return [{"code": s.value, "label": l} for s, l in SOURCE_LABELS.items() if s not in (SourceType.USER_INPUT, SourceType.CALCULATION, SourceType.AI_EXTRACTION)]


def parse_hint(document_type: str | None) -> SourceType | None:
    if not document_type:
        return None
    try:
        return SourceType(document_type)
    except ValueError as exc:
        raise ServiceError(400, "Unknown document type") from exc


def upload(case: TaxCase, filename: str, content_type: str, data: bytes, document_type: str | None, store, max_mb: int, allow_llm: bool = True) -> DocumentRecord:
    if len(data) > max_mb * 1024 * 1024:
        raise ServiceError(413, f"File exceeds {max_mb} MB")
    if not data:
        raise ServiceError(400, "Empty file")
    hint = parse_hint(document_type)
    raw = RawDocument(filename=filename or "document", mime_type=content_type or "", data=data)
    try:
        doc = ingest(case, raw, hint=hint, allow_llm=allow_llm)
    except DocumentError as exc:
        raise ServiceError(400, str(exc)) from exc
    store.put(doc.storage_key, data)
    return doc


def extraction_summary(doc: DocumentRecord) -> str:
    return (f"{SOURCE_LABELS.get(doc.type, doc.type.value)} processed – {len(doc.linked_entity_ids)} value(s) extracted at "
            f"{int(doc.extraction_confidence * 100)}% confidence")


def document_detail(case: TaxCase, document_id: str) -> dict:
    doc = case.document(document_id)
    if doc is None:
        raise ServiceError(404, "Document not found")
    linked = [case.find_entity(e) for e in doc.linked_entity_ids]
    return {"document": doc_view(doc), "linked_entities": [e.model_dump(mode="json") for e in linked if e is not None],
            "related_items": [i.model_dump(mode="json") for i in case.reconciliation_items if any(ev.document_id == document_id for ev in i.evidence)]}


def reprocess(case: TaxCase, document_id: str, document_type: str | None, store, allow_llm: bool = True) -> DocumentRecord:
    doc = case.document(document_id)
    if doc is None:
        raise ServiceError(404, "Document not found")
    data = store.get(doc.storage_key)
    hint = parse_hint(document_type) if document_type else doc.type
    for eid in doc.linked_entity_ids:
        case_ops.remove_entity(case, eid)
    case.documents = [d for d in case.documents if d.id != document_id]
    raw = RawDocument(filename=doc.filename, mime_type=doc.mime_type, data=data)
    try:
        return ingest(case, raw, hint=hint, storage_key=doc.storage_key, allow_llm=allow_llm)
    except DocumentError as exc:
        raise ServiceError(400, str(exc)) from exc


def delete_document(case: TaxCase, document_id: str, store) -> DocumentRecord:
    doc = case.document(document_id)
    if doc is None:
        raise ServiceError(404, "Document not found")
    for eid in doc.linked_entity_ids:
        case_ops.remove_entity(case, eid)
    for t in list(case.tax_deducted):
        if document_id in t.document_ids and len(t.document_ids) == 1:
            case.tax_deducted.remove(t)
    case.documents = [d for d in case.documents if d.id != document_id]
    try:
        store.delete(doc.storage_key)
    except Exception:  # noqa: BLE001
        pass
    return doc
