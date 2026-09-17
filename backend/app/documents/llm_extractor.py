"""Optional LLM-based extraction for scanned PDFs / images (used only when rule-based parsing finds no text).

The model is asked for a structured JSON document that mirrors the rule-based ``fields`` layout; the
result is marked ``extraction_method=LLM`` and ``AI_SUGGESTED`` so the taxpayer must confirm it.
Requires ANTHROPIC_API_KEY. Never called from the frontend directly.
"""
from __future__ import annotations

import base64
import json
import logging

from app.config import get_settings
from app.models.common import SourceType

log = logging.getLogger("astra.llm_extractor")

FIELD_SCHEMAS: dict[SourceType, dict] = {
    SourceType.FORM16: {
        "type": "object",
        "properties": {
            "employer_name": {"type": ["string", "null"]},
            "employer_tan": {"type": ["string", "null"]},
            "assessment_year": {"type": ["string", "null"]},
            "gross_salary": {"type": ["number", "null"]},
            "exempt_allowances_total": {"type": ["number", "null"]},
            "professional_tax": {"type": ["number", "null"]},
            "employer_nps_80ccd2": {"type": ["number", "null"]},
            "total_tds": {"type": ["number", "null"]},
            "confidence": {"type": "number"},
            "ambiguities": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["employer_name", "employer_tan", "assessment_year", "gross_salary", "exempt_allowances_total",
                     "professional_tax", "employer_nps_80ccd2", "total_tds", "confidence", "ambiguities"],
        "additionalProperties": False,
    },
    SourceType.INTEREST_CERTIFICATE: {
        "type": "object",
        "properties": {
            "bank_name": {"type": ["string", "null"]},
            "financial_year": {"type": ["string", "null"]},
            "rows": {"type": "array", "items": {"type": "object", "properties": {
                "account": {"type": ["string", "null"]}, "kind": {"type": "string", "enum": ["SAVINGS", "FIXED_DEPOSIT", "RECURRING_DEPOSIT", "OTHER"]},
                "interest": {"type": "number"}, "tds": {"type": "number"}}, "required": ["account", "kind", "interest", "tds"], "additionalProperties": False}},
            "confidence": {"type": "number"},
            "ambiguities": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["bank_name", "financial_year", "rows", "confidence", "ambiguities"],
        "additionalProperties": False,
    },
}

GENERIC_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string"},
        "issuer": {"type": ["string", "null"]},
        "period": {"type": ["string", "null"]},
        "amounts": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "amount": {"type": "number"}, "kind": {"type": "string", "enum": ["INCOME", "TAX_DEDUCTED", "DEDUCTION", "OTHER"]}},
            "required": ["label", "amount", "kind"], "additionalProperties": False}},
        "confidence": {"type": "number"},
        "ambiguities": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["document_type", "issuer", "period", "amounts", "confidence", "ambiguities"],
    "additionalProperties": False,
}

SYSTEM = (
    "You are a document-extraction component inside an Indian income-tax workspace. Extract only values that are "
    "explicitly present in the document. Never estimate or infer a number that is not printed. Use null when a field "
    "is absent. List every ambiguity (unclear digits, multiple candidate values, cropped tables) in `ambiguities`. "
    "Report `confidence` between 0 and 1 for the extraction as a whole."
)


def llm_available() -> bool:
    return get_settings().llm_available


def extract_with_llm(data: bytes, mime_type: str, doc_type: SourceType) -> dict | None:
    """Returns the parsed JSON fields or None when the LLM is unavailable / fails."""
    if not llm_available():
        return None
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return None
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    schema = FIELD_SCHEMAS.get(doc_type, GENERIC_SCHEMA)
    b64 = base64.standard_b64encode(data).decode("utf-8")
    if mime_type == "application/pdf":
        block = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
    elif mime_type in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        block = {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": b64}}
    else:
        return None
    try:
        response = client.messages.create(
            model=settings.astra_llm_model,
            max_tokens=4000,
            system=SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": [block, {"type": "text", "text": f"Extract the fields for a document of type {doc_type.value}."}]}],
        )
        if response.stop_reason == "refusal":
            log.warning("LLM extraction refused: %s", getattr(response, "stop_details", None))
            return None
        text = next((b.text for b in response.content if b.type == "text"), None)
        return json.loads(text) if text else None
    except anthropic.APIStatusError as exc:  # pragma: no cover - network
        log.warning("LLM extraction failed (%s)", exc.status_code)
        return None
    except (anthropic.APIConnectionError, json.JSONDecodeError) as exc:  # pragma: no cover - network
        log.warning("LLM extraction failed: %s", exc.__class__.__name__)
        return None
