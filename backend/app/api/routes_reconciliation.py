"""Reconciliation items, alerts and their resolution."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import current_user, get_repo, load_case
from app.api.serializers import case_view
from app.core import audit
from app.db.models import User
from app.db.repository import CaseRepository
from app.evidence.graph import entity_evidence
from app.models.common import SOURCE_LABELS, SourceType, ValueStatus, utcnow
from app.models.tax_model import TaxCase
from app.reconciliation.engine import reconcile
from app.services import case_ops
from app.tax_engine.engine import compute

router = APIRouter(prefix="/cases/{case_id}", tags=["reconciliation"])

PRIORITY = {"HIGH": {"label": "High priority", "icon": "🔴"}, "MEDIUM": {"label": "Review", "icon": "🟠"}, "LOW": {"label": "Review", "icon": "🟡"}, "INFO": {"label": "Information", "icon": "🔵"}}


class Resolution(BaseModel):
    action: str  # ADD_INCOME | REMOVE_DUPLICATE | CONFIRM_ENTITY | RECLASSIFY | REVIEW | DISMISS
    payload: dict[str, Any] | None = None
    note: str | None = None


def _matrix(case: TaxCase) -> list[dict]:
    """Category × source matrix of amounts for the reconciliation overview."""
    from collections import defaultdict

    from app.models.common import money

    cells: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    obs = [o for d in case.documents for o in d.observations]
    for o in obs:
        cat, st = o.get("category", ""), o.get("source_type", "")
        amt = float(o.get("amount") or 0)
        if cat in ("SALARY", "AIS_SALARY"):
            cells["Salary"][st] += amt
        elif cat == "SALARY_MONTH":
            cells["Salary"]["SALARY_SLIP"] += amt
        elif cat in ("INTEREST", "AIS_INTEREST"):
            cells["Interest"][st] += amt
        elif cat == "BANK_CREDIT" and o.get("kind") == "INTEREST" and not o.get("out_of_period"):
            cells["Interest"]["BANK_STATEMENT"] += amt
        elif cat in ("DIVIDEND", "AIS_DIVIDEND"):
            cells["Dividend"][st] += amt
        elif cat == "BANK_CREDIT" and o.get("kind") == "DIVIDEND":
            cells["Dividend"]["BANK_STATEMENT"] += amt
        elif cat in ("CAPITAL_GAIN_TXN", "AIS_SECURITIES_SALE"):
            cells["Sale of securities"][st] += amt
        elif cat in ("AIS_RENT",) or (cat == "BANK_CREDIT" and o.get("kind") == "RENT"):
            cells["Rent"][st] += amt
        elif cat in ("AIS_BUSINESS_RECEIPTS",):
            cells["Professional receipts"][st] += amt
        elif cat == "TDS":
            cells["TDS"][st] += float(o.get("tax_deducted") or 0)
    declared = {
        "Salary": float(sum((s.gross_salary.amount for s in case.income.salary), money(0))),
        "Interest": float(sum((i.amount.amount for i in case.income.interest), money(0))),
        "Dividend": float(sum((d.amount.amount for d in case.income.dividend), money(0))),
        "Sale of securities": float(sum((t.sale_consideration.amount for t in case.income.capital_gains), money(0))),
        "Rent": float(sum((r.annual_rent_received.amount for r in case.income.rental), money(0))),
        "Professional receipts": float(sum((b.gross_receipts.amount for b in case.income.business), money(0))),
        "TDS": float(compute(case).new.summary.tds),
    }
    sources = ["FORM16", "FORM16A", "SALARY_SLIP", "FORM26AS", "AIS", "INTEREST_CERTIFICATE", "BANK_STATEMENT", "BROKER_STATEMENT", "DIVIDEND_STATEMENT"]
    present = [s for s in sources if any(s in row for row in cells.values())]
    rows = []
    for cat, per in cells.items():
        values = {s: round(per.get(s, 0.0), 2) for s in present}
        values["RETURN"] = round(declared.get(cat, 0.0), 2)
        nonzero = [v for v in values.values() if v]
        status_ = "OK" if (len(set(round(v) for v in nonzero)) <= 1) else "REVIEW"
        rows.append({"category": cat, "values": values, "status": status_})
    return [{"sources": [{"code": s, "label": SOURCE_LABELS[SourceType(s)]} for s in present] + [{"code": "RETURN", "label": "In return"}], "rows": rows}]


@router.get("/reconciliation")
def get_reconciliation(case: TaxCase = Depends(load_case)):
    return {"items": [i.model_dump(mode="json") for i in case.reconciliation_items], "matrix": _matrix(case)[0],
            "last_run": case.meta.last_reconciliation_at.isoformat() if case.meta.last_reconciliation_at else None}


@router.post("/reconciliation/run")
def run_reconciliation(case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    items = reconcile(case)
    repo.save(case)
    audit.record(repo, case.id, user.id, audit.ACTOR_ASTRA, "reconciliation.run", f"Reconciliation run – {len([i for i in items if i.status == 'OPEN'])} open item(s)")
    return {"items": [i.model_dump(mode="json") for i in items], "matrix": _matrix(case)[0]}


@router.get("/issues")
def issues(case: TaxCase = Depends(load_case)):
    """Proactive alerts view: prioritised open items with explanation, evidence, impact and required action."""
    open_items = case.open_items()
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
    alerts = []
    for i in sorted(open_items, key=lambda x: order.get(x.severity, 9)):
        alerts.append({**i.model_dump(mode="json"), "priority": PRIORITY[i.severity], "explanation": i.description, "impact": (float(i.impact_estimate) if i.impact_estimate is not None else None),
                       "entities": [entity_evidence(case, e) for e in i.entity_ids][:3]})
    counts = {k: len([i for i in open_items if i.severity == k]) for k in ("HIGH", "MEDIUM", "LOW", "INFO")}
    return {"headline": f"ASTRA FOUND {len(open_items)} ITEM{'S' if len(open_items) != 1 else ''} TO REVIEW", "counts": counts, "alerts": alerts,
            "resolved": [i.model_dump(mode="json") for i in case.reconciliation_items if i.status != "OPEN"]}


@router.get("/reconciliation/{item_id}")
def get_item(item_id: str, case: TaxCase = Depends(load_case)):
    item = next((i for i in case.reconciliation_items if i.id == item_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    return {"item": item.model_dump(mode="json"), "entities": [entity_evidence(case, e) for e in item.entity_ids],
            "documents": [{"id": d.id, "filename": d.filename, "type": d.type.value} for d in case.documents if any(ev.document_id == d.id for ev in item.evidence)]}


@router.post("/reconciliation/{item_id}/resolve")
def resolve_item(item_id: str, body: Resolution, case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    item = next((i for i in case.reconciliation_items if i.id == item_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    action = body.action.upper()
    payload = body.payload or next((r.payload for r in item.suggested_resolutions if r.action == action), {}) or {}
    summary = ""
    if action == "ADD_INCOME":
        head = payload.get("head")
        if not head:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "payload.head required")
        try:
            entity = case_ops.build_entity(head, {**payload, "reference": f"Confirmed from reconciliation item {item.title}"}, ValueStatus.USER_CONFIRMED)
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid payload: {exc}") from exc
        # carry provenance of the evidence so the number stays traceable to the original documents
        for ev in item.evidence:
            if ev.document_id:
                for name, value in entity.__dict__.items():
                    if name in ("amount", "gross_salary", "annual_rent_received", "gross_receipts") and hasattr(value, "provenance"):
                        from app.models.common import Provenance
                        value.provenance.append(Provenance(source_type=ev.source_type, document_id=ev.document_id, reference=ev.reference or ev.label, confidence=0.95))
        case_ops.collection_for(case, head).append(entity)
        item.entity_ids.append(entity.id)
        summary = f"User confirmed {head.replace('_', ' ').lower()} income from '{item.title}'"
        if head in ("RENTAL", "CAPITAL_GAINS", "BUSINESS", "DIVIDEND", "INTEREST", "SALARY", "OTHER") and head not in case.taxpayer.income_sources:
            case.taxpayer.income_sources.append(head)
    elif action == "REMOVE_DUPLICATE":
        eid = payload.get("entity_id")
        if not eid or not case_ops.remove_entity(case, eid):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "entity_id required / not found")
        summary = "Duplicate entry removed"
    elif action == "CONFIRM_ENTITY":
        if payload.get("prefer_source"):
            for t in case.tax_deducted:
                if t.source_type.value == payload["prefer_source"] and (t.deductor_tan == payload.get("key") or True) and t.section.replace("-", "").upper() == str(payload.get("section", "")).upper():
                    if any(t.id == e.entity_id for e in item.evidence):
                        t.status = ValueStatus.USER_CONFIRMED
                        t.tax_deducted = t.tax_deducted.confirmed()
        elif payload.get("entity_id"):
            case_ops.confirm_entity(case, payload["entity_id"])
        summary = "User confirmed the existing figure"
    elif action == "RECLASSIFY":
        eid = payload.get("entity_id")
        changes = {k: v for k, v in payload.items() if k not in ("entity_id",)}
        if not eid or not case_ops.update_entity(case, eid, changes):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "entity_id required / not found")
        summary = "User reclassified / corrected an entry"
    elif action == "REVIEW":
        summary = "User marked the item as reviewed"
    elif action == "DISMISS":
        if not body.note:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "A reason is required to dismiss an item")
        summary = "User dismissed the item"
    elif action in ("UPLOAD_DOCUMENT", "UPDATE_PROFILE"):
        return {"navigate": "documents" if action == "UPLOAD_DOCUMENT" else "profile", "case": case_view(case)}
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown action")
    item.status = "DISMISSED" if action == "DISMISS" else "RESOLVED"
    item.resolution_note = body.note or summary
    item.resolved_at = utcnow()
    case_ops.commit(repo, case, user.id, audit.ACTOR_USER, "reconciliation.resolved", f"{summary}: {item.title}", {"item_id": item.id, "action": action, "note": body.note},
                    [{"label": e.label, "document_id": e.document_id} for e in item.evidence[:4]])
    current = next((i for i in case.reconciliation_items if i.id == item_id), item)
    return {"item": current.model_dump(mode="json"), "case": case_view(case)}


@router.post("/reconciliation/{item_id}/reopen")
def reopen_item(item_id: str, case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    item = next((i for i in case.reconciliation_items if i.id == item_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    item.status, item.resolution_note, item.resolved_at = "OPEN", None, None
    case_ops.commit(repo, case, user.id, audit.ACTOR_USER, "reconciliation.reopened", f"Item reopened: {item.title}", {"item_id": item.id})
    return {"item": item.model_dump(mode="json")}
