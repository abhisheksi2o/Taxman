"""API-facing views of the model – PAN is masked, documents never expose raw content."""
from __future__ import annotations

from app.models.common import SOURCE_LABELS, mask_pan
from app.models.tax_model import DocumentRecord, TaxCase


def case_view(case: TaxCase) -> dict:
    data = case.model_dump(mode="json")
    data["taxpayer"]["pan_masked"] = mask_pan(case.taxpayer.pan)
    data["taxpayer"]["pan"] = None
    for d in data["documents"]:
        d.pop("storage_key", None)
    return data


def profile_view(case: TaxCase) -> dict:
    tp = case.taxpayer.model_dump(mode="json")
    tp["pan_masked"] = mask_pan(case.taxpayer.pan)
    tp["pan"] = None
    return tp


def doc_view(d: DocumentRecord) -> dict:
    v = d.model_dump(mode="json")
    v.pop("storage_key", None)
    v["type_label"] = SOURCE_LABELS.get(d.type, d.type.value)
    return v
