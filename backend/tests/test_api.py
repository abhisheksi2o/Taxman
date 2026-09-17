"""End-to-end API flow: demo login → generate → dashboard → resolve → ask Astra → review → evaluation."""
from app.models.common import SourceType
from app.synthetic.generator import generate


def test_unauthenticated_is_rejected(client):
    client.cookies.clear()
    assert client.get("/api/cases").status_code == 401


def test_full_flow(demo_client):
    c = demo_client
    me = c.get("/api/auth/me").json()
    assert me["is_demo"] and me["dev_mode"]

    r = c.post("/api/demo/generate", json={"scenario": "salaried_interest", "seed": 42})
    assert r.status_code == 201, r.text
    case = r.json()["case"]
    cid = case["id"]
    assert case["taxpayer"]["pan"] is None and case["taxpayer"]["pan_masked"]
    assert r.json()["open_items"] == 2

    dash = c.get(f"/api/cases/{cid}/dashboard").json()
    assert dash["alerts"]["count"] == 2 and dash["readiness"]["score"] < 100

    comp = c.get(f"/api/cases/{cid}/computation").json()
    assert comp["rules_version"].startswith("AY2026-27")
    exp = c.get(f"/api/cases/{cid}/computation/explain/NEW/salary.standard_deduction").json()
    assert "narrative" in exp and "16(ia)" in exp["narrative"]

    recon = c.get(f"/api/cases/{cid}/reconciliation").json()
    item = next(i for i in recon["items"] if i["kind"] == "MISSING" and "Savings" in i["title"])
    r = c.post(f"/api/cases/{cid}/reconciliation/{item['id']}/resolve", json={"action": "ADD_INCOME"})
    assert r.status_code == 200, r.text
    assert r.json()["item"]["status"] == "RESOLVED"
    interest = [i for i in r.json()["case"]["income"]["interest"] if i["kind"] == "SAVINGS"]
    assert interest and interest[0]["status"] == "USER_CONFIRMED"
    assert any(p["source_type"] == "BANK_STATEMENT" for p in interest[0]["amount"]["provenance"])  # provenance carried over

    r = c.post(f"/api/cases/{cid}/astra/ask", json={"question": "What information is missing?"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "deterministic" and body["grounding"]["verified"]
    assert "Fixed deposit" in body["answer"] or "fixed deposit" in body["answer"]

    ev = c.get(f"/api/cases/{cid}/evidence/{interest[0]['id']}").json()
    assert ev["fields"] and ev["fields"][0]["sources"]

    review = c.get(f"/api/cases/{cid}/review").json()
    assert review["package"]["itr_form"] in ("ITR-1", "ITR-2")
    decl = {d["code"]: True for d in review["package"]["declarations"]}
    r = c.post(f"/api/cases/{cid}/review/confirm", json={"declarations": decl, "selected_regime": "NEW"})
    assert r.status_code == 409  # a HIGH item is still open
    r = c.post(f"/api/cases/{cid}/review/confirm", json={"declarations": decl, "selected_regime": "NEW", "acknowledge_open_issues": True})
    assert r.status_code == 200, r.text
    assert c.get(f"/api/cases/{cid}/review/package").status_code == 200

    audit = c.get(f"/api/cases/{cid}/audit").json()["events"]
    actions = {e["action"] for e in audit}
    assert {"document.uploaded", "document.extracted", "reconciliation.run", "reconciliation.resolved", "astra.answered", "review.confirmed"} <= actions

    hidden = c.get(f"/api/dev/evaluation/case/{cid}").json()
    assert hidden["scenario"] == "salaried_interest" and all(h["detected"] for h in hidden["hidden_issues"])

    r = c.post("/api/dev/evaluation/run", json={"seed": 42, "scenarios": ["missing_income", "tds_mismatch"], "include_qa": True})
    assert r.status_code == 200
    assert r.json()["detection_rate"] == 1.0


def test_upload_and_csrf_and_isolation(demo_client):
    c = demo_client
    r = c.post("/api/cases", json={"assessment_year": "2026-27"})
    cid = r.json()["id"]
    gen = generate("salaried_basic", 21)
    f16 = next(d for d in gen.documents if d.doc_type == SourceType.FORM16)
    r = c.post(f"/api/cases/{cid}/documents", files={"file": (f16.filename, f16.data, "application/pdf")})
    assert r.status_code == 201, r.text
    doc = r.json()["document"]
    assert doc["type"] == "FORM16" and doc["extraction_confidence"] > 0.8 and "storage_key" not in doc
    # duplicate upload rejected
    assert c.post(f"/api/cases/{cid}/documents", files={"file": (f16.filename, f16.data, "application/pdf")}).status_code == 400
    # CSRF: cookie session without the client header must be refused for mutations
    del c.headers["x-astra-client"]
    assert c.post(f"/api/cases/{cid}/reconciliation/run").status_code == 403
    c.headers.update({"x-astra-client": "web"})
    # another user cannot see this case
    cookies = dict(c.cookies)
    c.cookies.clear()
    c.post("/api/auth/demo")
    assert c.get(f"/api/cases/{cid}").status_code == 404
    c.cookies.clear()
    for k, v in cookies.items():
        c.cookies.set(k, v)
    assert c.get(f"/api/cases/{cid}").status_code == 200


def test_rules_endpoint(demo_client):
    r = demo_client.get("/api/rules/2026-27").json()
    assert r["regimes"]["NEW"]["rebate"]["income_threshold"] == 1200000.0
    assert demo_client.get("/api/rules/2019-20").status_code == 404
