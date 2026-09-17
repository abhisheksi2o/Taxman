"""AI evaluation framework: runs every demo scenario through the real pipeline and scores Astra.

For each hidden issue we record: whether it was detected, the evidence Astra used, the correct impact
vs Astra's impact, and whether Astra's answers contain amounts that do not exist in the grounded model
(hallucination detection). False positives (HIGH/MEDIUM items with no hidden counterpart) are counted too.
"""
from __future__ import annotations

import re
import time
from decimal import Decimal

from app.documents.extractors.common import normalize_key
from app.models.common import money, utcnow
from app.models.tax_model import ReconciliationItem, TaxCase
from app.reconciliation.engine import reconcile
from app.synthetic.generator import compute_expected_impacts, generate, list_scenarios, materialize
from app.synthetic.scenarios import HiddenIssue
from app.tax_engine.engine import compute, fmt

from app.ai.grounding import AMOUNT_RE, unverified_amounts  # noqa: E402


def item_matches(h: HiddenIssue, item: ReconciliationItem) -> bool:
    m = h.match
    if item.kind != m.get("kind") or item.category != m.get("category"):
        return False
    key = (m.get("key") or "").upper()
    fp = item.fingerprint.upper()
    hay = fp + " " + " ".join((e.label or "") + " " + (e.reference or "") for e in item.evidence).upper() + " " + item.title.upper()
    if key and key not in hay and normalize_key(key) not in hay.replace(" ", ""):
        tan = (m.get("tan") or "").upper()
        if not (tan and tan in hay):
            return False
    if m.get("sub_kind") and m["sub_kind"].upper() not in fp:
        return False
    if m.get("section") and m["section"] not in fp:
        return False
    return True


def grounded_amounts(case: TaxCase, computation) -> set[str]:
    """Every rupee figure that legitimately exists in the model / computation / items (as Indian-formatted strings)."""
    values: set[str] = set()

    def add(v) -> None:
        try:
            d = money(v)
        except Exception:  # noqa: BLE001
            return
        values.add(fmt(abs(d)).lstrip("₹-"))
        values.add(fmt(abs(d)).lstrip("₹-").split(".")[0])

    def walk(obj) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("amount", "tax_deducted", "impact_estimate", "gross", "cost", "principal", "processed_value", "derived_value", "credit", "debit", "balance", "interest", "tds",
                         "sell_value", "buy_value", "amount_paid", "total_tds", "total_interest", "total_amount_paid", "gross_salary", "used", "difference", "old", "new") and isinstance(v, (int, float, str, Decimal)):
                    add(v)
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
        elif isinstance(obj, (int, float, Decimal)):
            add(obj)

    walk(case.model_dump(mode="json"))
    walk(computation.model_dump(mode="json"))
    for line_group in (computation.old.lines, computation.new.lines):
        for l in line_group:
            _walk_lines(l, add)
    return values


def _walk_lines(line, add) -> None:
    add(line.amount)
    for c in line.children:
        _walk_lines(c, add)


def hallucinated_amounts(answer: str, grounded: set[str]) -> list[str]:
    return unverified_amounts(answer, grounded)


def _mention(m: str) -> str:
    m = m.lower()
    return m[:-3] if m.endswith(".00") else m


def evaluate_scenario(code: str, seed: int, assessment_year: str, ask=None) -> dict:
    t0 = time.perf_counter()
    gen = generate(code, seed, assessment_year)
    case = materialize(gen, owner_user_id="eval")
    compute_expected_impacts(case, gen.hidden_issues)
    items = reconcile(case)
    computation = compute(case)
    grounded = grounded_amounts(case, computation)
    results = []
    matched_ids: set[str] = set()
    for h in gen.hidden_issues:
        hit = next((i for i in items if item_matches(h, i) and i.id not in matched_ids), None)
        if hit:
            matched_ids.add(hit.id)
        astra_impact = float(hit.impact_estimate) if (hit and hit.impact_estimate is not None) else None
        expected = h.expected_impact
        if h.impact_mode == "NONE":
            impact_ok = True
        elif expected is None or astra_impact is None:
            impact_ok = hit is not None and expected in (None, 0.0) and astra_impact in (None, 0.0)
        else:
            impact_ok = abs(abs(expected) - abs(astra_impact)) <= max(10.0, abs(expected) * 0.01)
        evidence_ok = hit is not None and all(any(exp.lower() in ((e.label or "") + " " + (e.reference or "")).lower() for e in hit.evidence) for exp in h.expected_evidence)
        results.append({
            "id": h.id, "kind": h.kind, "category": h.category, "severity": h.severity, "title": h.title, "hidden_issue": h.description, "correct_answer": h.correct_answer,
            "detected": hit is not None, "detected_item": ({"id": hit.id, "title": hit.title, "severity": hit.severity, "description": hit.description} if hit else None),
            "expected_evidence": h.expected_evidence, "evidence_used": [e.label for e in hit.evidence] if hit else [], "evidence_ok": evidence_ok,
            "correct_impact": expected, "astra_impact": astra_impact, "impact_ok": impact_ok,
            "result": "PASS" if (hit is not None and impact_ok and evidence_ok) else ("PARTIAL" if hit is not None else "FAIL"),
        })
    false_positives = [{"id": i.id, "title": i.title, "severity": i.severity, "category": i.category, "kind": i.kind}
                       for i in items if i.id not in matched_ids and i.severity in ("HIGH", "MEDIUM") and i.status == "OPEN"]
    # Astra Q&A
    qa = []
    if ask is not None:
        for q in gen.questions:
            try:
                answer = ask(case, q["question"], computation)
            except Exception as exc:  # noqa: BLE001
                answer = {"answer": f"[error: {exc.__class__.__name__}]", "tools": [], "evidence": []}
            text = answer.get("answer", "")
            # amounts that are neither in the model/computation nor in any tool output the answer was built from
            own = answer.get("grounding", {}).get("unverified_amounts")
            halluc = [a for a in (own if own is not None else hallucinated_amounts(text, grounded)) if a.lstrip("₹") not in grounded and a.lstrip("₹").split(".")[0] not in grounded]
            norm = text.lower()
            mentions_ok = all(_mention(m) in norm for m in q.get("must_mention", []))
            qa.append({"question": q["question"], "answer": text, "expected": q.get("answer_key"), "must_mention": q.get("must_mention", []), "mentions_ok": mentions_ok,
                       "hallucinated_amounts": halluc, "tools": answer.get("tools", []), "evidence": answer.get("evidence", []), "mode": answer.get("mode"),
                       "result": "PASS" if (mentions_ok and not halluc) else "FAIL"})
    detected = sum(1 for r in results if r["detected"])
    return {
        "scenario": code, "label": gen.label, "description": gen.description, "seed": seed, "assessment_year": assessment_year, "story": gen.story,
        "hidden_issue_count": len(results), "detected": detected, "passed": sum(1 for r in results if r["result"] == "PASS"), "false_positives": false_positives,
        "items_total": len(items), "hidden_issues": results, "qa": qa, "qa_passed": sum(1 for q in qa if q["result"] == "PASS"),
        "documents": [{"filename": d.filename, "type": d.type.value, "confidence": d.extraction_confidence, "status": d.status, "flags": d.flags} for d in case.documents],
        "computation_summary": {"regime": "NEW", "taxable_income": float(computation.new.summary.taxable_income), "total_tax": float(computation.new.summary.total_tax_liability),
                                "net_payable": float(computation.new.summary.net_payable)},
        "duration_ms": int((time.perf_counter() - t0) * 1000),
    }


def evaluate_all(seed: int = 42, assessment_year: str = "2026-27", scenarios: list[str] | None = None, ask=None) -> dict:
    codes = scenarios or [s["code"] for s in list_scenarios()]
    cases = [evaluate_scenario(c, seed, assessment_year, ask) for c in codes]
    total_hidden = sum(c["hidden_issue_count"] for c in cases)
    return {
        "run_at": utcnow().isoformat(), "seed": seed, "assessment_year": assessment_year, "scenarios": len(cases),
        "hidden_issues": total_hidden, "detected": sum(c["detected"] for c in cases), "passed": sum(c["passed"] for c in cases),
        "false_positives": sum(len(c["false_positives"]) for c in cases), "qa_total": sum(len(c["qa"]) for c in cases), "qa_passed": sum(c["qa_passed"] for c in cases),
        "detection_rate": round(sum(c["detected"] for c in cases) / total_hidden, 3) if total_hidden else None,
        "cases": cases,
    }
