"""Ask Astra – conversational tax investigator.

Two execution modes with an identical contract:
* ``llm``            – Claude with tool calling over the structured model (requires ANTHROPIC_API_KEY).
* ``deterministic``  – keyword intent router + templated answers built from the same tools (always available;
                       used for offline demos and for the evaluation benchmark).
Both return {answer, mode, tools, evidence, grounding}. Every figure is checked against the grounded amount set.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.ai.grounding import collect_amounts, unverified_amounts
from app.ai.prompts import SYSTEM_PROMPT
from app.ai.tools import ToolContext, anthropic_tool_defs, run_tool
from app.config import get_settings
from app.models.tax_model import TaxCase
from app.reconciliation.impact import working_regime
from app.tax_engine.engine import compute
from app.tax_engine.schema import TaxComputation

log = logging.getLogger("astra.ai")

SUGGESTED_QUESTIONS = [
    "Why is my tax higher this year?",
    "Why is my refund different?",
    "What income have I reported?",
    "What information is missing?",
    "Why is there a mismatch?",
    "Explain my capital gains.",
    "Show me everything contributing to my taxable income.",
    "What documents are still required?",
    "Compare the old and new regime for me.",
]


class AstraResult(dict):
    pass


def ask(case: TaxCase, question: str, computation: TaxComputation | None = None, history: list[dict] | None = None, mode: str | None = None) -> dict:
    computation = computation or compute(case)
    ctx = ToolContext(case=case, computation=computation, regime=working_regime(case))
    settings = get_settings()
    use_llm = (mode == "llm") or (mode is None and settings.llm_available)
    if use_llm:
        try:
            return _ask_llm(ctx, question, history or [])
        except Exception as exc:  # noqa: BLE001 – always degrade gracefully
            log.warning("LLM path failed (%s); falling back to deterministic mode", exc.__class__.__name__)
            result = _ask_deterministic(ctx, question)
            result["mode"] = "deterministic-fallback"
            return result
    return _ask_deterministic(ctx, question)


# --------------------------------------------------------------------------- shared helpers


def _evidence_from_tool_outputs(outputs: list[dict]) -> list[dict]:
    seen = set()
    out = []

    def walk(o):
        if isinstance(o, dict):
            if "label" in o and ("document_id" in o or "source_type" in o):
                key = (o.get("label"), o.get("document_id"))
                if key not in seen:
                    seen.add(key)
                    out.append({"label": o.get("label"), "document_id": o.get("document_id"), "amount": o.get("amount")})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for o in outputs:
        walk(o)
    return out[:12]


def _finalize(ctx: ToolContext, answer: str, tools_used: list[dict], mode: str) -> dict:
    grounded = collect_amounts([t["output"] for t in tools_used])
    grounded |= collect_amounts(ctx.computation.model_dump(mode="json"))
    unverified = unverified_amounts(answer, grounded)
    if unverified:
        answer += "\n\n_Note: the figure(s) " + ", ".join(unverified) + " could not be verified against the tax model – treat them as indicative and check the Tax Computation page._"
    return {"answer": answer, "mode": mode, "tools": [{"name": t["name"], "args": t["args"]} for t in tools_used],
            "evidence": _evidence_from_tool_outputs([t["output"] for t in tools_used]),
            "grounding": {"unverified_amounts": unverified, "verified": not unverified, "regime": ctx.regime, "rules_version": ctx.computation.rules_version},
            "labels": ["AI-generated explanation", "Figures: deterministic engine"]}


# --------------------------------------------------------------------------- LLM mode


def _ask_llm(ctx: ToolContext, question: str, history: list[dict]) -> dict:
    import anthropic

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    messages: list[dict] = []
    for h in history[-8:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": question})
    tools_used: list[dict] = []
    final_text = ""
    for _ in range(8):
        response = client.messages.create(
            model=settings.astra_llm_model,
            max_tokens=4000,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=anthropic_tool_defs(),
            messages=messages,
        )
        if response.stop_reason == "refusal":
            final_text = "I can't help with that request here. Please ask about the figures, documents or issues in this return."
            break
        tool_blocks = [b for b in response.content if b.type == "tool_use"]
        text_blocks = [b.text for b in response.content if b.type == "text"]
        if not tool_blocks:
            final_text = "\n".join(text_blocks).strip()
            break
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for tb in tool_blocks:
            args = tb.input if isinstance(tb.input, dict) else json.loads(json.dumps(tb.input))
            output = run_tool(ctx, tb.name, args)
            tools_used.append({"name": tb.name, "args": args, "output": output})
            results.append({"type": "tool_result", "tool_use_id": tb.id, "content": json.dumps(output, default=str)[:60000]})
        messages.append({"role": "user", "content": results})
        if response.stop_reason == "max_tokens":
            final_text = "\n".join(text_blocks).strip() or "The answer was cut short – please ask a narrower question."
            break
    else:
        final_text = "I gathered the data but could not finish the explanation. Please try a narrower question."
    return _finalize(ctx, final_text or "I could not produce an answer from the available data.", tools_used, "llm")


# --------------------------------------------------------------------------- deterministic mode

INTENTS: list[tuple[str, list[str]]] = [
    ("documents", [r"\bdocuments?\b", r"\bupload", r"\bproofs?\b"]),
    ("missing", [r"\bmissing\b", r"\bstill (?:need|required)\b", r"\bincomplete\b", r"what (?:else|more) do", r"\bwhat.*required\b"]),
    ("capital_gains", [r"capital gain", r"\bshares?\b", r"\bstocks?\b", r"mutual fund", r"\bltcg\b", r"\bstcg\b", r"\bequity\b", r"\btrades?\b"]),
    ("regime", [r"\bregime", r"\bold vs new\b", r"\bcompare\b", r"115bac"]),
    ("previous_year", [r"\bhigher\b", r"\blower\b", r"last year", r"previous year", r"year[- ]on[- ]year", r"\bthis year\b"]),
    ("refund", [r"\brefund", r"\bpayable", r"\bbalance\b", r"how much tax", r"\bestimat", r"\bowe\b", r"\bdue\b"]),
    ("issues", [r"\bmismatch", r"\bdiffer", r"\bdiscrepan", r"\breconcil", r"\bduplicate", r"\btwice\b", r"\bissue", r"\balert", r"\bproblem", r"\bflag"]),
    ("deductions", [r"\bdeduction", r"\b80[a-z]{1,3}\b", r"\binvestment", r"\bppf\b", r"\belss\b", r"\bnps\b", r"\binsurance"]),
    ("tds", [r"\btds\b", r"tax deducted", r"26as", r"\bcredit"]),
    ("income", [r"\bincome\b", r"\breported\b", r"\bcontribut", r"\btaxable\b", r"\bsalary\b", r"\binterest\b", r"\bdividend", r"\brent"]),
]


def detect_intent(question: str) -> str:
    q = question.lower()
    for intent, patterns in INTENTS:
        if any(re.search(p, q) for p in patterns):
            return intent
    return "summary"


def _call(ctx: ToolContext, tools_used: list[dict], name: str, **args) -> dict:
    out = run_tool(ctx, name, args)
    tools_used.append({"name": name, "args": args, "output": out})
    return out


def _sev_icon(sev: str) -> str:
    return {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟡", "INFO": "🔵"}.get(sev, "•")


def _ask_deterministic(ctx: ToolContext, question: str) -> dict:
    intent = detect_intent(question)
    tools_used: list[dict] = []
    lines: list[str] = []
    summary = _call(ctx, tools_used, "get_tax_summary", regime=ctx.regime)
    f = summary["figures"]
    regime_word = "new" if ctx.regime == "NEW" else "old"

    if intent == "missing":
        m = _call(ctx, tools_used, "get_missing_information")
        lines.append(f"**Return readiness: {m['readiness_score']}%.** {m['summary']}")
        if m["missing_income_items"]:
            lines.append("\n**Income that appears in your documents but not in the return (requires review):**")
            for i in m["missing_income_items"]:
                ev = ", ".join(e["label"] for e in i["evidence"][:3])
                lines.append(f"- {_sev_icon(i['severity'])} {i['title']} — {i['description']}" + (f" Evidence: {ev}." if ev else "") + (f" Estimated impact: {i['impact_fmt']}." if i.get("impact_fmt") else ""))
        else:
            lines.append("\nNo income visible in your documents is unaccounted for.")
        if m["required_missing"]:
            lines.append("\n**Required items still missing:**")
            lines += [f"- {r['label']} — {r['why']}" for r in m["required_missing"]]
        if m["recommended_missing"]:
            lines.append("\n**Recommended:** " + "; ".join(r["label"] for r in m["recommended_missing"]) + ".")
        if m["unconfirmed_values"]:
            lines.append(f"\n{m['unconfirmed_values']} extracted value(s) still need your confirmation in My Tax Profile.")
        lines.append("\nNext step: open Issues & Alerts to confirm or add each item, then upload the missing documents.")

    elif intent == "documents":
        d = _call(ctx, tools_used, "get_required_documents")
        missing = [x for x in d["documents"] if x["status"] == "MISSING"]
        have = [x for x in d["documents"] if x["status"] == "SATISFIED"]
        if missing:
            lines.append("**Documents still required or recommended:**")
            lines += [f"- {x['label']} ({x['priority'].lower()}) — {x['why']}" for x in missing]
        else:
            lines.append("All required documents are available.")
        if have:
            lines.append("\n**Already available:** " + "; ".join(x["label"] for x in have) + ".")
        needs = [u for u in d["uploaded"] if u["status"] != "EXTRACTED"]
        if needs:
            lines.append("\n**Uploaded documents needing attention:** " + "; ".join(f"{u['filename']} ({', '.join(u['flags']) or u['status'].lower()})" for u in needs) + ".")
        lines.append("\nNext step: upload the required documents on the Documents page; Astra re-reconciles automatically.")

    elif intent == "issues":
        o = _call(ctx, tools_used, "get_open_issues", severity=None, include_resolved=False)
        if o["count"] == 0:
            lines.append("There are no open reconciliation items – all sources agree with the return as it stands.")
        else:
            lines.append(f"**Astra found {o['count']} item{'s' if o['count'] != 1 else ''} to review.** Each one lists the sources that disagree; none of them implies wrongdoing.")
            for i in o["items"]:
                ev = "; ".join(f"{e['label']}" + (f" ({_amt(e['amount'])})" if e.get("amount") else "") for e in i["evidence"][:3])
                lines.append(f"\n{_sev_icon(i['severity'])} **{i['title']}** ({i['kind'].lower()}, {i['severity'].lower()} priority)\n{i['description']}" + (f"\nEvidence: {ev}." if ev else "") + (f"\nEstimated impact: {i['impact_fmt']} — {i['impact_note']}" if i.get("impact_fmt") else "") + f"\nRequired action: {i['required_action']}")
        lines.append("\nNext step: resolve each item from Issues & Alerts; every resolution is recorded in the audit trail.")

    elif intent == "capital_gains":
        cg = _call(ctx, tools_used, "get_capital_gains_detail")
        if not cg["transactions"]:
            lines.append("No capital-gains transactions are recorded in this return. If you sold shares, mutual-fund units or property, upload the broker's tax P&L statement.")
        else:
            lines.append(f"**Net capital gains ({regime_word} regime): {cg['total_fmt']}.** Per transaction:")
            for t in cg["transactions"]:
                notes = " ".join(t.get("notes", []))
                lines.append(f"- {t['label']}: gain {t['amount_fmt']} — {notes} Rule: {t.get('rule')}.")
            if cg["buckets"]:
                lines.append("\n**By tax bucket:** " + "; ".join(f"{b['label']} {b['amount_fmt']}" for b in cg["buckets"]) + ".")
            for sp in cg["tax_on_special_income"]:
                lines.append(f"**Tax on special-rate income: {sp['amount_fmt']}** — " + "; ".join(f"{c['label']} {c['amount_fmt']} ({c['formula']})" for c in sp.get("children", [])) + ".")
            lines.append(f"\n{cg['rules']['note']}")
        issues = [i for i in _call(ctx, tools_used, "get_open_issues", severity=None, include_resolved=False)["items"] if i["category"] == "CAPITAL_GAINS"]
        for i in issues:
            lines.append(f"\n{_sev_icon(i['severity'])} Requires review: {i['title']} — {i['description']}")
        lines.append("\nNext step: confirm each trade's classification in Reconciliation, then re-check the computation.")

    elif intent == "regime":
        c = _call(ctx, tools_used, "compare_regimes")
        lines.append("**Old vs new regime (current data):**")
        for r in c["rows"]:
            lines.append(f"- {r['label']}: old {r['old_fmt']} · new {r['new_fmt']} · difference {r['difference_fmt']}")
        lines.append("")
        lines += [f"- {e}" for e in c["explanation"]]
        lines.append("\n**Assumptions:** " + " ".join(c["assumptions"][:4]))
        lines.append("\nNext step: choose the regime in Return Review – Astra does not choose for you.")

    elif intent == "previous_year":
        p = _call(ctx, tools_used, "compare_with_previous_year")
        if not p["previous_available"]:
            lines.append(f"I don't have last year's return, so I can't compare. This year's estimate under the {regime_word} regime: total tax {f['total_tax_liability']['fmt']}, credits {f['total_credits']['fmt']}, " + (f"refund {f['refund_due']['fmt']}" if summary["position"] == "refund" else f"balance payable {f['balance_payable']['fmt']}") + ".")
            lines.append("\nNext step: upload last year's ITR acknowledgement (JSON) on the Documents page to enable the comparison.")
        else:
            lines.append(f"**Compared with AY {p['previous']['assessment_year']}:**")
            lines += [f"- {r}" for r in p["reasons"]]
            o = _call(ctx, tools_used, "get_open_issues", severity=None, include_resolved=False)
            if o["count"]:
                lines.append(f"\n{o['count']} open reconciliation item(s) may still change these figures – e.g. {o['items'][0]['title']}.")
            lines.append("\nNext step: review the regime comparison and resolve open items before comparing final figures.")

    elif intent == "refund":
        t = _call(ctx, tools_used, "get_taxes_paid")
        pos = summary["position"]
        headline = (f"an estimated refund of {f['refund_due']['fmt']}" if pos == "refund" else (f"an estimated balance payable of {f['balance_payable']['fmt']}" if pos == "payable" else "no tax due and no refund"))
        lines.append(f"**Under the {regime_word} regime you have {headline}.**")
        lines.append(f"- Total tax liability: {f['total_tax_liability']['fmt']} (taxable income {f['taxable_income']['fmt']}, rebate {f['rebate_87a']['fmt']}, cess {f['cess']['fmt']})")
        lines.append(f"- Taxes already paid: {f['total_credits']['fmt']} (TDS {f['tds']['fmt']}, advance tax {f['advance_tax']['fmt']}, self-assessment {f['self_assessment_tax']['fmt']})")
        if float(f["total_interest"]["amount"]) > 0:
            lines.append(f"- Interest u/s 234A/B/C (estimate): {f['total_interest']['fmt']}")
        conflicts = [l for l in t["ledger"] if l.get("conflict")]
        for l in conflicts:
            cands = ", ".join(f"{c['source']} {_amt(c['amount'])}" for c in l["candidates"])
            lines.append(f"- ⚠️ TDS from {l['deductor']} differs between sources ({cands}); {_amt(l['used'])} from {l['used_source']} is used pending reconciliation.")
        o = _call(ctx, tools_used, "get_open_issues", severity=None, include_resolved=False)
        impactful = [i for i in o["items"] if i.get("impact_fmt")]
        if impactful:
            lines.append("\n**Open items that can change this figure:**")
            for i in impactful[:5]:
                lines.append(f"- {_sev_icon(i['severity'])} {i['title']} — estimated impact {i['impact_fmt']} ({i['impact_note']})")
        if summary["lower_tax_regime_with_current_data"] not in (ctx.regime, "EQUAL"):
            lines.append(f"- The {summary['lower_tax_regime_with_current_data'].lower()} regime would give a lower liability with the current data – see the regime comparison.")
        lines.append(f"\nRules: {summary['rules_version']} (statutory default regime: {summary['statutory_default_regime'].lower()}). Estimates only; open items may change them.")
        lines.append("\nNext step: open Tax Computation to expand every line and its sources.")

    elif intent == "deductions":
        d = _call(ctx, tools_used, "get_deductions")
        for regime in ("OLD", "NEW"):
            r = d["by_regime"][regime]
            lines.append(f"**{regime.title()} regime – deductions allowed: {r['total_fmt']}**")
            for s in r["sections"]:
                note = " ".join(s.get("notes", []))
                lines.append(f"- {s['label']}: {s['amount_fmt']}" + (f" ({s['formula']})" if s.get("formula") else "") + (f" — {note}" if note else ""))
        lines.append(f"\n{d['note']}")
        lines.append("\nNext step: attach proofs for each claim on the Documents page so the Before-You-File check can mark them supported.")

    elif intent == "tds":
        t = _call(ctx, tools_used, "get_taxes_paid")
        lines.append(f"**Taxes already paid: {t['total_credits_fmt']}**")
        for l in t["lines"]:
            lines.append(f"- {l['label']}: {l['amount_fmt']}")
            for c in l.get("children", []):
                lines.append(f"  - {c['label']}: {c['amount_fmt']}" + (f" — {' '.join(c['notes'])}" if c.get("notes") else "") + (f" [{', '.join(c['sources'])}]" if c.get("sources") else ""))
        lines.append("\nWhen sources disagree, the Form 26AS figure is used until the item is resolved (credit is limited to what the department has on record).")
        lines.append("\nNext step: resolve any TDS mismatch in Reconciliation.")

    elif intent == "income":
        b = _call(ctx, tools_used, "get_income_breakdown", head="ALL")
        lines.append(f"**Gross total income ({regime_word} regime): {b['gross_total_income_fmt']}**")
        for h in b["heads"]:
            if not h.get("children") and float(h["amount"]) == 0:
                continue
            lines.append(f"\n**{h['label']}: {h['amount_fmt']}**" + (f" — {h['formula']}" if h.get("formula") else ""))
            for c in h.get("children", []):
                src = ", ".join(c.get("sources", [])[:2])
                status = c.get("status")
                lines.append(f"- {c['label']}: {c['amount_fmt']}" + (f" [{status.lower().replace('_', ' ')}]" if status else "") + (f" — sources: {src}" if src else ""))
                for g in c.get("children", [])[:8]:
                    gsrc = ", ".join(g.get("sources", [])[:2])
                    lines.append(f"  - {g['label']}: {g['amount_fmt']}" + (f" — {gsrc}" if gsrc else ""))
        lines.append(f"\nTaxable income after deductions: {f['taxable_income']['fmt']}; total tax {f['total_tax_liability']['fmt']}.")
        lines.append("\nNext step: confirm every extracted value in My Tax Profile; click any number in Tax Computation to see why it is there.")

    else:
        o = _call(ctx, tools_used, "get_open_issues", severity=None, include_resolved=False)
        pos = summary["position"]
        lines.append(f"**Snapshot ({regime_word} regime, rules {summary['rules_version']}):** gross total income {f['gross_total_income']['fmt']}, taxable income {f['taxable_income']['fmt']}, total tax {f['total_tax_liability']['fmt']}, taxes paid {f['total_credits']['fmt']} → " + (f"estimated refund {f['refund_due']['fmt']}" if pos == "refund" else f"estimated payable {f['balance_payable']['fmt']}") + ".")
        if o["count"]:
            lines.append(f"\n{o['count']} item(s) require review: " + "; ".join(f"{_sev_icon(i['severity'])} {i['title']}" for i in o["items"][:4]) + ".")
        lines.append("\nYou can ask me things like: " + " · ".join(SUGGESTED_QUESTIONS[:5]))
        lines.append("\nNext step: pick a question above or open Issues & Alerts.")

    return _finalize(ctx, "\n".join(lines), tools_used, "deterministic")


def _amt(v) -> str:
    from app.models.common import money
    from app.tax_engine.engine import fmt

    return fmt(money(v))
