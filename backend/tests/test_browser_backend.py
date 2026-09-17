"""The in-browser backend (Pyodide edition) must serve the same contract as the HTTP API. This runs it natively."""
import json

from app.browser.backend import BrowserBackend
from app.models.common import SourceType
from app.synthetic.generator import generate


def call(b, method, path, body=None, file=None):
    res = json.loads(b.handle(method, path, json.dumps(body) if body is not None else None, *(file or (None, None, None))))
    return res["status"], res["body"]


def test_browser_backend_full_flow_and_persistence():
    b = BrowserBackend("")
    assert call(b, "GET", "/auth/me")[1]["dev_mode"] is True
    st, gen = call(b, "POST", "/demo/generate", {"scenario": "salaried_interest", "seed": 42, "assessment_year": "2026-27"})
    assert st == 201 and gen["open_items"] == 2
    cid = gen["case"]["id"]
    assert gen["case"]["taxpayer"]["pan"] is None and gen["case"]["taxpayer"]["pan_masked"]

    st, dash = call(b, "GET", f"/cases/{cid}/dashboard")
    assert st == 200 and dash["alerts"]["count"] == 2 and 0 < dash["readiness"]["score"] < 100

    st, comp = call(b, "GET", f"/cases/{cid}/computation")
    assert comp["rules_version"].startswith("AY2026-27")
    st, exp = call(b, "GET", f"/cases/{cid}/computation/explain/NEW/salary.standard_deduction")
    assert st == 200 and "16(ia)" in exp["narrative"]

    st, recon = call(b, "GET", f"/cases/{cid}/reconciliation")
    item = next(i for i in recon["items"] if i["kind"] == "MISSING" and "Savings" in i["title"])
    st, res = call(b, "POST", f"/cases/{cid}/reconciliation/{item['id']}/resolve", {"action": "ADD_INCOME"})
    assert st == 200 and res["item"]["status"] == "RESOLVED"
    assert any(i["kind"] == "SAVINGS" and i["status"] == "USER_CONFIRMED" for i in res["case"]["income"]["interest"])

    st, ans = call(b, "POST", f"/cases/{cid}/astra/ask", {"question": "What information is missing?"})
    assert st == 200 and ans["mode"] == "deterministic" and ans["grounding"]["verified"]

    # upload through the file path (bytes as the worker would pass them)
    g = generate("salaried_basic", 9)
    f16 = next(d for d in g.documents if d.doc_type == SourceType.FORM16)
    st, newcase = call(b, "POST", "/cases", {"assessment_year": "2026-27"})
    st, up = call(b, "POST", f"/cases/{newcase['id']}/documents", {"document_type": None}, (f16.filename, "application/pdf", f16.data))
    assert st == 201 and up["document"]["type"] == "FORM16" and up["document"]["extraction_confidence"] > 0.8

    st, review = call(b, "GET", f"/cases/{cid}/review")
    decl = {d["code"]: True for d in review["package"]["declarations"]}
    st, _ = call(b, "POST", f"/cases/{cid}/review/confirm", {"declarations": decl, "selected_regime": "NEW"})
    assert st == 409  # a HIGH item is still open
    st, done = call(b, "POST", f"/cases/{cid}/review/confirm", {"declarations": decl, "selected_regime": "NEW", "acknowledge_open_issues": True})
    assert st == 200 and done["review_state"]["confirmed"]
    st, pkg = call(b, "GET", f"/cases/{cid}/review/package")
    assert st == 200 and pkg["__blob__"] and json.loads(pkg["content"])["itr_form"]

    st, hidden = call(b, "GET", f"/dev/evaluation/case/{cid}")
    assert st == 200 and all(h["detected"] for h in hidden["hidden_issues"])
    st, trail = call(b, "GET", f"/cases/{cid}/audit")
    assert {"document.extracted", "reconciliation.resolved", "astra.answered", "review.confirmed"} <= {e["action"] for e in trail["events"]}

    # persistence round-trip: export → new backend → same cases, blobs and history
    assert b.dirty
    state = b.export_state()
    b2 = BrowserBackend(state)
    st, cases = call(b2, "GET", "/cases")
    assert {c["id"] for c in cases["cases"]} == {cid, newcase["id"]}
    st, pkg2 = call(b2, "GET", f"/cases/{cid}/review/package")
    assert st == 200 and pkg2["content"] == pkg["content"]
    assert call(b2, "GET", f"/cases/{cid}/audit")[1]["events"][0]["action"] == trail["events"][0]["action"]
    assert call(b2, "GET", "/nope")[0] == 404


def test_browser_backend_evaluation_run():
    b = BrowserBackend("")
    st, res = call(b, "POST", "/dev/evaluation/run", {"seed": 42, "scenarios": ["missing_income"], "include_qa": True})
    assert st == 200 and res["detection_rate"] == 1.0 and res["false_positives"] == 0
    assert call(b, "GET", "/dev/evaluation/runs")[1]["runs"][0]["id"] == res["run_id"]
