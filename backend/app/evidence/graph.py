"""Evidence graph: every number → its sources → the documents that carry them."""
from __future__ import annotations

from decimal import Decimal

from app.models.common import SOURCE_LABELS, money
from app.models.tax_model import TaxCase
from app.tax_engine.engine import fmt
from app.tax_engine.schema import TaxComputation

ZERO = Decimal("0")


def _tv_nodes(tv, label: str, case: TaxCase) -> list[dict]:
    nodes = []
    for p in tv.provenance:
        doc = case.document(p.document_id) if p.document_id else None
        nodes.append({"label": SOURCE_LABELS.get(p.source_type, p.source_type.value), "source_type": p.source_type.value, "document_id": p.document_id,
                      "document_name": doc.filename if doc else None, "reference": p.reference, "confidence": p.confidence, "page": p.page, "note": p.note, "field": label})
    if not nodes:
        nodes.append({"label": "User input", "source_type": "USER_INPUT", "document_id": None, "document_name": None, "reference": None, "confidence": 1.0, "page": None, "note": None, "field": label})
    return nodes


def entity_evidence(case: TaxCase, entity_id: str) -> dict | None:
    e = case.find_entity(entity_id)
    if e is None:
        return None
    fields: list[dict] = []
    for name, value in e.__dict__.items():
        if hasattr(value, "provenance") and hasattr(value, "amount"):
            fields.append({"field": name, "amount": float(value.amount), "status": value.status.value, "sources": _tv_nodes(value, name, case)})
    related_items = [{"id": i.id, "kind": i.kind, "severity": i.severity, "title": i.title, "status": i.status} for i in case.reconciliation_items if entity_id in i.entity_ids]
    docs = [{"id": d.id, "filename": d.filename, "type": d.type.value, "status": d.status, "confidence": d.extraction_confidence} for d in case.documents if entity_id in d.linked_entity_ids or d.id in getattr(e, "document_ids", [])]
    return {"entity_id": entity_id, "entity_type": type(e).__name__, "label": _entity_label(e), "status": getattr(e, "status", None).value if getattr(e, "status", None) else None,
            "fields": fields, "documents": docs, "reconciliation_items": related_items,
            "reconciliation_status": ("REVIEW_REQUIRED" if any(i["status"] == "OPEN" for i in related_items) else "OK")}


def _entity_label(e) -> str:
    for attr in ("employer_name", "payer_name", "property_name", "description", "deductor_name", "lender", "bank_name", "filename"):
        if getattr(e, attr, None):
            return getattr(e, attr)
    return type(e).__name__


def income_evidence_tree(case: TaxCase, computation: TaxComputation, regime: str = "NEW") -> list[dict]:
    """For the "Why is this number here?" panel: heads → entities → sources, with reconciliation status."""
    rc = computation.regime(regime)
    out = []
    for top in rc.lines:
        if top.id not in ("salary", "hp", "cg", "business", "os", "credits"):
            continue
        node = {"id": top.id, "label": top.label, "amount": float(top.amount), "children": []}
        for child in top.children:
            leaf = {"id": child.id, "label": child.label, "amount": float(child.amount), "entity_id": child.entity_id, "status": child.status.value if child.status else None,
                    "sources": [s.model_dump(mode="json") for s in child.sources], "formula": child.formula, "notes": child.notes}
            if child.entity_id:
                related = [i for i in case.reconciliation_items if child.entity_id in i.entity_ids and i.status == "OPEN"]
                leaf["reconciliation"] = "REVIEW_REQUIRED" if related else "OK"
                leaf["related_items"] = [{"id": i.id, "title": i.title, "severity": i.severity} for i in related]
            if child.children:
                leaf["children"] = [{"id": g.id, "label": g.label, "amount": float(g.amount), "formula": g.formula, "sources": [s.model_dump(mode="json") for s in g.sources], "notes": g.notes} for g in child.children]
            node["children"].append(leaf)
        out.append(node)
    return out


def number_explanation(case: TaxCase, computation: TaxComputation, regime: str, line_id: str) -> dict | None:
    from app.tax_engine.engine import explain_line

    exp = explain_line(computation, regime, line_id)
    if exp is None:
        return None
    line = exp["line"]
    sources = []
    for s in line.get("sources", []):
        doc = case.document(s["document_id"]) if s.get("document_id") else None
        sources.append({**s, "document_name": doc.filename if doc else None, "document_status": doc.status if doc else None})
    ent = case.find_entity(line["entity_id"]) if line.get("entity_id") else None
    related = [i for i in case.reconciliation_items if line.get("entity_id") and line["entity_id"] in i.entity_ids]
    narrative = _narrative(line, exp)
    return {**exp, "sources": sources, "entity": entity_evidence(case, ent.id) if ent else None,
            "related_items": [{"id": i.id, "title": i.title, "severity": i.severity, "status": i.status} for i in related],
            "reconciliation_status": ("REVIEW_REQUIRED" if any(i.status == "OPEN" for i in related) else "OK"), "narrative": narrative}


def _narrative(line: dict, exp: dict) -> str:
    amount = fmt(money(line["amount"]))
    parts = [f"{line['label']} is {amount}."]
    if line.get("formula"):
        parts.append(f"It is computed as {line['formula']}.")
    if line.get("rule_ref"):
        parts.append(f"Rule applied: {line['rule_ref']}.")
    if exp.get("inputs"):
        parts.append("It is made up of " + "; ".join(f"{i['label']} {fmt(money(i['amount']))}" for i in exp["inputs"][:6]) + ("…" if len(exp["inputs"]) > 6 else "") + ".")
    if line.get("sources"):
        parts.append("Sources: " + ", ".join(s["label"] for s in line["sources"][:4]) + ".")
    if line.get("notes"):
        parts.append(" ".join(line["notes"]))
    return " ".join(parts)
