"""Tools Ask Astra can call. Each returns JSON built strictly from the structured model, the deterministic
computation, the reconciliation items and the readiness report – never from free text."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.evidence.graph import entity_evidence, number_explanation
from app.models.common import SOURCE_LABELS, money
from app.models.tax_model import TaxCase
from app.readiness.checker import check as readiness_check
from app.readiness.requirements import requirements_for
from app.tax_engine.engine import fmt
from app.tax_engine.schema import TaxComputation

ZERO = Decimal("0")


@dataclass
class ToolContext:
    case: TaxCase
    computation: TaxComputation
    regime: str

    @property
    def rc(self):
        return self.computation.regime(self.regime)


def _f(v) -> str:
    return fmt(money(v))


def _line_dict(line, depth: int = 1) -> dict:
    d = {"id": line.id, "label": line.label, "amount": float(line.amount), "amount_fmt": _f(line.amount), "formula": line.formula, "rule": line.rule_ref,
         "status": line.status.value if line.status else None, "sources": [s.label for s in line.sources][:4], "notes": line.notes}
    if depth > 0 and line.children:
        d["children"] = [_line_dict(c, depth - 1) for c in line.children]
    return d


# --------------------------------------------------------------------------- tool implementations


def get_tax_summary(ctx: ToolContext, regime: str | None = None) -> dict:
    regime = regime or ctx.regime
    rc = ctx.computation.regime(regime)
    s = rc.summary
    return {
        "regime": regime, "regime_label": rc.label, "assessment_year": ctx.computation.assessment_year, "rules_version": ctx.computation.rules_version,
        "statutory_default_regime": ctx.computation.statutory_default_regime, "lower_tax_regime_with_current_data": ctx.computation.comparison.lower_tax_regime,
        "figures": {k: {"amount": float(getattr(s, k)), "fmt": _f(getattr(s, k))} for k in
                    ("gross_total_income", "total_deductions", "taxable_income", "tax_before_rebate", "rebate_87a", "surcharge", "cess", "total_tax_liability",
                     "tds", "tcs", "advance_tax", "self_assessment_tax", "total_credits", "total_interest", "net_payable", "refund_due", "balance_payable")},
        "position": ("refund" if s.net_payable < 0 else ("payable" if s.net_payable > 0 else "nil")),
        "warnings": rc.warnings, "assumptions": rc.assumptions[:6], "validation_warnings": ctx.computation.validation_warnings,
        "open_issue_count": len(ctx.case.open_items()),
    }


def get_income_breakdown(ctx: ToolContext, head: str = "ALL") -> dict:
    rc = ctx.rc
    id_map = {"SALARY": "salary", "HOUSE_PROPERTY": "hp", "CAPITAL_GAINS": "cg", "BUSINESS": "business", "OTHER_SOURCES": "os"}
    wanted = [id_map[head]] if head in id_map else list(id_map.values())
    heads = []
    for top in rc.lines:
        if top.id in wanted:
            heads.append(_line_dict(top, depth=2))
    gti = next((l for l in rc.lines if l.id == "gti"), None)
    return {"regime": ctx.regime, "heads": heads, "gross_total_income": float(gti.amount) if gti else None, "gross_total_income_fmt": _f(gti.amount) if gti else None,
            "status_legend": {"EXTRACTED": "read from a document, not yet confirmed", "USER_CONFIRMED": "confirmed by you", "USER_ENTERED": "entered by you",
                              "CALCULATED": "computed by the tax engine", "AI_SUGGESTED": "proposed by AI extraction, needs confirmation", "UNRESOLVED": "sources conflict"}}


def get_deductions(ctx: ToolContext) -> dict:
    out = {}
    for regime in ("OLD", "NEW"):
        rc = ctx.computation.regime(regime)
        via = next((l for l in rc.lines if l.id == "via"), None)
        out[regime] = {"total": float(via.amount) if via else 0.0, "total_fmt": _f(via.amount) if via else "₹0", "sections": [_line_dict(c, depth=1) for c in via.children] if via else []}
    return {"by_regime": out, "note": "Only 80CCD(2) is available under the new regime; other Chapter VI-A deductions apply to the old regime."}


def get_taxes_paid(ctx: ToolContext) -> dict:
    rc = ctx.rc
    credits = next((l for l in rc.lines if l.id == "credits"), None)
    return {"regime": ctx.regime, "total_credits": float(credits.amount) if credits else 0.0, "total_credits_fmt": _f(credits.amount) if credits else "₹0",
            "lines": [_line_dict(c, depth=1) for c in credits.children] if credits else [], "ledger": rc.credit_ledger}


def get_open_issues(ctx: ToolContext, severity: str | None = None, include_resolved: bool = False) -> dict:
    items = ctx.case.reconciliation_items if include_resolved else ctx.case.open_items()
    if severity:
        items = [i for i in items if i.severity == severity]
    return {"count": len(items), "items": [{
        "id": i.id, "kind": i.kind, "category": i.category, "severity": i.severity, "status": i.status, "title": i.title, "description": i.description,
        "impact_estimate": float(i.impact_estimate) if i.impact_estimate is not None else None, "impact_fmt": _f(i.impact_estimate) if i.impact_estimate is not None else None,
        "impact_note": i.impact_note, "required_action": i.required_action, "evidence": [{"label": e.label, "amount": float(e.amount) if e.amount is not None else None, "document_id": e.document_id} for e in i.evidence],
        "resolutions": [r.label for r in i.suggested_resolutions]} for i in items]}


def get_issue_detail(ctx: ToolContext, item_id: str) -> dict:
    i = next((x for x in ctx.case.reconciliation_items if x.id == item_id), None)
    if i is None:
        return {"error": f"No item {item_id}"}
    return {**get_open_issues(ctx, include_resolved=True)["items"][[x.id for x in ctx.case.reconciliation_items].index(item_id)], "entity_ids": i.entity_ids, "resolution_note": i.resolution_note}


def get_evidence(ctx: ToolContext, entity_id: str) -> dict:
    return entity_evidence(ctx.case, entity_id) or {"error": f"No entity {entity_id}"}


def explain_line(ctx: ToolContext, line_id: str, regime: str | None = None) -> dict:
    return number_explanation(ctx.case, ctx.computation, regime or ctx.regime, line_id) or {"error": f"No line {line_id}"}


def compare_regimes(ctx: ToolContext) -> dict:
    c = ctx.computation.comparison
    return {"rows": [{"key": r.key, "label": r.label, "old": float(r.old), "old_fmt": _f(r.old), "new": float(r.new), "new_fmt": _f(r.new), "difference": float(r.difference), "difference_fmt": _f(r.difference)} for r in c.rows],
            "lower_tax_regime": c.lower_tax_regime, "difference": float(c.difference), "difference_fmt": _f(c.difference), "explanation": c.explanation, "assumptions": c.assumptions,
            "statutory_default": c.statutory_default, "note": "Astra does not choose a regime for you."}


def get_missing_information(ctx: ToolContext) -> dict:
    report = readiness_check(ctx.case, ctx.computation)
    reqs = requirements_for(ctx.case)
    missing_items = [i for i in ctx.case.open_items() if i.kind == "MISSING"]
    return {"readiness_score": report.score, "summary": report.summary, "blocking_checks": report.blocking,
            "missing_income_items": get_open_issues(ctx)["items"] and [x for x in get_open_issues(ctx)["items"] if x["kind"] == "MISSING"],
            "required_missing": [r.as_dict() for r in reqs if r.status == "MISSING" and r.priority == "REQUIRED"],
            "recommended_missing": [r.as_dict() for r in reqs if r.status == "MISSING" and r.priority == "RECOMMENDED"],
            "unconfirmed_values": report.unconfirmed_values, "checks": [{"label": c.label, "status": c.status, "findings": c.findings[:5]} for c in report.checks]}


def get_required_documents(ctx: ToolContext) -> dict:
    reqs = requirements_for(ctx.case)
    return {"documents": [r.as_dict() for r in reqs if r.kind == "DOCUMENT"], "information": [r.as_dict() for r in reqs if r.kind == "INFORMATION"],
            "uploaded": [{"id": d.id, "filename": d.filename, "type": d.type.value, "status": d.status, "confidence": d.extraction_confidence, "flags": d.flags} for d in ctx.case.documents]}


def compare_with_previous_year(ctx: ToolContext) -> dict:
    prev = ctx.case.previous_return
    s = ctx.rc.summary
    current = {"gross_total_income": float(s.gross_total_income), "total_deductions": float(s.total_deductions), "taxable_income": float(s.taxable_income),
               "total_tax": float(s.total_tax_liability), "tds": float(s.tds), "net_payable": float(s.net_payable), "regime": ctx.regime}
    if prev is None:
        return {"previous_available": False, "current": {k: (v if isinstance(v, str) else {"amount": v, "fmt": _f(v)}) for k, v in current.items()},
                "note": "No previous return uploaded – upload last year's ITR acknowledgement / JSON to compare."}
    previous = {"assessment_year": prev.assessment_year, "regime": prev.regime, "gross_total_income": float(prev.gross_total_income), "total_deductions": float(prev.total_deductions),
                "taxable_income": float(prev.taxable_income), "total_tax": float(prev.total_tax), "tds": float(prev.tds), "refund_or_payable": float(prev.refund_or_payable),
                "income_heads": {k: float(v) for k, v in prev.income_heads.items()}}
    reasons = []
    d_gti = money(current["gross_total_income"]) - money(previous["gross_total_income"])
    if d_gti != 0:
        reasons.append(f"Gross total income moved by {_f(d_gti)} ({_f(previous['gross_total_income'])} → {_f(current['gross_total_income'])}).")
    d_ded = money(current["total_deductions"]) - money(previous["total_deductions"])
    if d_ded != 0:
        reasons.append(f"Deductions changed by {_f(d_ded)}.")
    if prev.regime and prev.regime != ctx.regime:
        reasons.append(f"Regime differs: {prev.regime.lower()} last year vs {ctx.regime.lower()} in this estimate – slab rates and available deductions differ.")
    d_tax = money(current["total_tax"]) - money(previous["total_tax"])
    reasons.append(f"Total tax: {_f(previous['total_tax'])} last year vs {_f(current['total_tax'])} now ({'higher' if d_tax > 0 else 'lower' if d_tax < 0 else 'unchanged'} by {_f(abs(d_tax))}).")
    d_tds = money(current["tds"]) - money(previous["tds"])
    if d_tds != 0:
        reasons.append(f"TDS credit: {_f(previous['tds'])} last year vs {_f(current['tds'])} now.")
    return {"previous_available": True, "previous": previous, "current": current, "reasons": reasons,
            "formatted": {"prev_tax": _f(previous["total_tax"]), "cur_tax": _f(current["total_tax"]), "prev_gti": _f(previous["gross_total_income"]), "cur_gti": _f(current["gross_total_income"])}}


def get_capital_gains_detail(ctx: ToolContext) -> dict:
    rc = ctx.rc
    cg = next((l for l in rc.lines if l.id == "cg"), None)
    if cg is None or not cg.children:
        return {"transactions": [], "buckets": [], "total": 0.0, "note": "No capital-gains transactions recorded."}
    txns = [_line_dict(c, depth=1) for c in cg.children if c.entity_id]
    buckets = [_line_dict(c, depth=0) for c in cg.children if c.id.startswith("cg.bucket")]
    special = [_line_dict(c, depth=1) for c in rc.lines if c.id == "tax.special"]
    return {"transactions": txns, "buckets": buckets, "tax_on_special_income": special, "total": float(cg.amount), "total_fmt": _f(cg.amount),
            "rules": {"ltcg_112a_exemption": "₹1,25,000", "note": "Listed equity / equity MF held > 12 months → LTCG u/s 112A (12.5% above ₹1.25 lakh); otherwise STCG u/s 111A (20%)."}}


def get_document_summary(ctx: ToolContext, document_id: str | None = None) -> dict:
    docs = [d for d in ctx.case.documents if document_id is None or d.id == document_id]
    return {"documents": [{"id": d.id, "filename": d.filename, "type": d.type.value, "type_label": SOURCE_LABELS[d.type], "status": d.status, "confidence": d.extraction_confidence,
                           "method": d.extraction_method, "flags": d.flags, "period": d.period_label, "linked_entities": d.linked_entity_ids,
                           "fields": {k: v for k, v in d.extracted_fields.items() if not isinstance(v, (list, dict))}} for d in docs]}


# --------------------------------------------------------------------------- registry (Anthropic tool schema + callable)

TOOLS: dict[str, tuple[str, dict, Callable[..., dict]]] = {
    "get_tax_summary": ("Total income, deductions, tax, credits and the estimated refund/payable for a regime, with rules version and warnings.",
                        {"type": "object", "properties": {"regime": {"type": ["string", "null"], "enum": ["OLD", "NEW", None]}}, "required": ["regime"], "additionalProperties": False}, get_tax_summary),
    "get_income_breakdown": ("Every income entity by head with amounts, status (extracted/confirmed/calculated) and sources.",
                             {"type": "object", "properties": {"head": {"type": "string", "enum": ["ALL", "SALARY", "HOUSE_PROPERTY", "CAPITAL_GAINS", "BUSINESS", "OTHER_SOURCES"]}}, "required": ["head"], "additionalProperties": False}, get_income_breakdown),
    "get_deductions": ("Chapter VI-A deductions claimed and allowed under each regime.", {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, get_deductions),
    "get_taxes_paid": ("TDS / TCS / advance tax credit ledger, including which source was used when sources conflict.", {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, get_taxes_paid),
    "get_open_issues": ("Reconciliation items (missing, mismatch, duplicate, classification, timing) with evidence, impact and required action.",
                        {"type": "object", "properties": {"severity": {"type": ["string", "null"], "enum": ["HIGH", "MEDIUM", "LOW", "INFO", None]}, "include_resolved": {"type": "boolean"}}, "required": ["severity", "include_resolved"], "additionalProperties": False}, get_open_issues),
    "get_issue_detail": ("Full detail of one reconciliation item.", {"type": "object", "properties": {"item_id": {"type": "string"}}, "required": ["item_id"], "additionalProperties": False}, get_issue_detail),
    "get_evidence": ("Provenance of an income / deduction / TDS entity: every field, its source document and confidence.", {"type": "object", "properties": {"entity_id": {"type": "string"}}, "required": ["entity_id"], "additionalProperties": False}, get_evidence),
    "explain_line": ("Explain one line of the tax computation: formula, rule, inputs and sources.", {"type": "object", "properties": {"line_id": {"type": "string"}, "regime": {"type": ["string", "null"], "enum": ["OLD", "NEW", None]}}, "required": ["line_id", "regime"], "additionalProperties": False}, explain_line),
    "compare_regimes": ("Side-by-side old vs new regime figures with the numerical explanation and assumptions.", {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, compare_regimes),
    "get_missing_information": ("Readiness score, missing income items, required documents / information still missing, unconfirmed values.", {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, get_missing_information),
    "get_required_documents": ("Which documents are required / recommended and which are already uploaded.", {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, get_required_documents),
    "compare_with_previous_year": ("Previous return vs current estimate with the reasons for the change.", {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, compare_with_previous_year),
    "get_capital_gains_detail": ("Per-transaction capital gains with short/long classification, buckets and special-rate tax.", {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, get_capital_gains_detail),
    "get_document_summary": ("Uploaded documents with type, confidence, flags and extracted fields.", {"type": "object", "properties": {"document_id": {"type": ["string", "null"]}}, "required": ["document_id"], "additionalProperties": False}, get_document_summary),
}


def anthropic_tool_defs() -> list[dict]:
    return [{"name": name, "description": desc, "input_schema": schema, "strict": True} for name, (desc, schema, _) in TOOLS.items()]


def run_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> dict:
    if name not in TOOLS:
        return {"error": f"unknown tool {name}"}
    fn = TOOLS[name][2]
    clean = {k: v for k, v in (args or {}).items() if v is not None}
    try:
        return fn(ctx, **clean)
    except TypeError as exc:
        return {"error": f"bad arguments for {name}: {exc}"}
