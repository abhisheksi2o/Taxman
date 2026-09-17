"""ASTRA Tax in-browser backend.

Serves the exact API contract of the FastAPI server, but everything runs inside the user's browser
(Pyodide) with an in-memory repository persisted to IndexedDB by the worker. There is no network, no
account and no server; the deterministic engine, extraction, reconciliation, readiness, evidence graph,
deterministic Astra and the evaluation benchmark are the same modules the server uses.
"""
from __future__ import annotations

import base64
import json
import os
import re
import traceback
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("ASTRA_LLM_ENABLED", "off")
os.environ.setdefault("ASTRA_DEMO_MODE", "true")
os.environ.setdefault("ASTRA_DEV_MODE", "true")

from app.ai.astra import SUGGESTED_QUESTIONS, ask  # noqa: E402
from app.evaluation.runner import evaluate_all  # noqa: E402
from app.evidence.graph import entity_evidence, income_evidence_tree, number_explanation  # noqa: E402
from app.models.common import ValueStatus, new_id, utcnow  # noqa: E402
from app.models.tax_model import TaxCase  # noqa: E402
from app.readiness.requirements import next_onboarding_step, requirements_for  # noqa: E402
from app.reconciliation.engine import reconcile  # noqa: E402
from app.reconciliation.impact import working_regime  # noqa: E402
from app.services import case_ops  # noqa: E402
from app.services import dashboard_service, demo_service, document_service, eval_service, review_service  # noqa: E402
from app.services import reconciliation_service as recon_svc  # noqa: E402
from app.services.errors import ServiceError  # noqa: E402
from app.services.views import case_view, doc_view, profile_view  # noqa: E402
from app.synthetic.generator import list_scenarios  # noqa: E402
from app.tax_engine.engine import compute  # noqa: E402
from app.tax_engine.rules import CURRENT_ASSESSMENT_YEAR, get_rules, supported_years  # noqa: E402

LOCAL_USER_ID = "local-user"
USER = {"id": LOCAL_USER_ID, "email": "you@this-device", "display_name": "You", "role": "developer", "is_demo": True, "dev_mode": True, "llm_enabled": False}
ALLOWED_PROFILE_FIELDS = {"assessment_year", "name", "pan", "date_of_birth", "taxpayer_type", "residential_status", "employment_status", "income_sources", "employer_count",
                          "has_investments", "has_rental_income", "property_count", "has_home_loan", "has_foreign_income_or_assets", "filed_previous_return", "city_type",
                          "regime_preference", "email", "phone", "address", "onboarding_completed"}


class MemoryStore:
    def __init__(self, blobs: dict[str, bytes] | None = None):
        self.blobs: dict[str, bytes] = blobs or {}

    def put(self, key: str, data: bytes) -> None:
        self.blobs[key] = bytes(data)

    def get(self, key: str) -> bytes:
        return self.blobs[key]

    def delete(self, key: str) -> None:
        self.blobs.pop(key, None)


class MemoryRepo:
    """Same surface as app.db.repository.CaseRepository, kept entirely in memory."""

    def __init__(self):
        self.cases: dict[str, TaxCase] = {}
        self.hidden: dict[str, dict] = {}
        self.conversations: dict[str, list[dict]] = {}
        self.audit_events: dict[str, list[dict]] = {}
        self.eval_runs: list[dict] = []
        self._audit_seq = 0

    def create(self, case: TaxCase, hidden_test: dict | None = None) -> None:
        self.cases[case.id] = case
        if hidden_test:
            self.hidden[case.id] = hidden_test

    def list_for_user(self, user_id: str) -> list[dict]:
        out = []
        for c in sorted(self.cases.values(), key=lambda c: c.meta.updated_at, reverse=True):
            out.append({"id": c.id, "label": c.label, "assessment_year": c.assessment_year, "updated_at": c.meta.updated_at.isoformat(), "taxpayer_name": c.taxpayer.name,
                        "demo_scenario": c.meta.demo_scenario, "onboarding_completed": c.taxpayer.onboarding_completed, "open_issues": len(c.open_items()), "documents": len(c.documents)})
        return out

    def get(self, case_id: str, user_id: str | None = None) -> TaxCase | None:
        return self.cases.get(case_id)

    def get_hidden_test(self, case_id: str, user_id: str | None = None) -> dict | None:
        return self.hidden.get(case_id)

    def save(self, case: TaxCase) -> None:
        case.touch()
        self.cases[case.id] = case

    def delete(self, case_id: str, user_id: str | None = None) -> list[str]:
        case = self.cases.pop(case_id, None)
        self.hidden.pop(case_id, None)
        self.conversations.pop(case_id, None)
        self.audit_events.pop(case_id, None)
        return [d.storage_key for d in case.documents] if case else []

    def get_conversation(self, case_id: str) -> list[dict]:
        return list(self.conversations.get(case_id, []))

    def save_conversation(self, case_id: str, messages: list[dict]) -> None:
        self.conversations[case_id] = messages[-40:]

    def audit(self, case_id: str, user_id: str | None, actor: str, action: str, summary: str, details: dict | None = None, evidence: list[dict] | None = None) -> None:
        self._audit_seq += 1
        self.audit_events.setdefault(case_id, []).append({"id": self._audit_seq, "ts": datetime.now(timezone.utc).isoformat(), "actor": actor, "action": action, "summary": summary[:400],
                                                          "details": json.loads(json.dumps(details or {}, default=str)), "evidence": json.loads(json.dumps(evidence or [], default=str))})

    def audit_trail(self, case_id: str, limit: int = 200) -> list[dict]:
        return list(reversed(self.audit_events.get(case_id, [])))[:limit]

    def save_eval_run(self, user_id: str, results: dict) -> str:
        run_id = new_id("eval")
        self.eval_runs.insert(0, {"id": run_id, "ts": datetime.now(timezone.utc).isoformat(), "results": json.loads(json.dumps(results, default=str))})
        self.eval_runs = self.eval_runs[:10]
        return run_id

    def list_eval_runs(self, user_id: str, limit: int = 10) -> list[dict]:
        return self.eval_runs[:limit]

    # ---- persistence -------------------------------------------------------------------------
    def dump(self, store: MemoryStore) -> str:
        return json.dumps({
            "version": 1,
            "cases": {cid: json.loads(c.model_dump_json()) for cid, c in self.cases.items()},
            "hidden": self.hidden, "conversations": self.conversations, "audit": self.audit_events, "audit_seq": self._audit_seq, "eval_runs": self.eval_runs,
            "blobs": {k: base64.b64encode(v).decode() for k, v in store.blobs.items()},
        }, default=str)

    @classmethod
    def load(cls, raw: str | None) -> tuple["MemoryRepo", MemoryStore]:
        repo = cls()
        store = MemoryStore()
        if not raw:
            return repo, store
        try:
            data = json.loads(raw)
            for cid, c in data.get("cases", {}).items():
                try:
                    repo.cases[cid] = TaxCase.model_validate(c)
                except Exception:  # noqa: BLE001 – skip cases from an incompatible older build
                    continue
            repo.hidden = data.get("hidden", {})
            repo.conversations = data.get("conversations", {})
            repo.audit_events = data.get("audit", {})
            repo._audit_seq = int(data.get("audit_seq", 0))
            repo.eval_runs = data.get("eval_runs", [])
            store.blobs = {k: base64.b64decode(v) for k, v in data.get("blobs", {}).items()}
        except Exception:  # noqa: BLE001 – corrupt state must not brick the app
            return cls(), MemoryStore()
        return repo, store


def _to_bytes(value) -> bytes | None:
    """JS Uint8Array / bytes-like → bytes; JS null / undefined (Pyodide's jsnull) / None → None."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return None
    try:
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value)
        if hasattr(value, "to_bytes"):
            return bytes(value.to_bytes())
        if hasattr(value, "to_py"):
            return bytes(value.to_py())
    except Exception:  # noqa: BLE001 – anything unconvertible means "no file"
        return None
    return None


class Response:
    def __init__(self, body: Any, status: int = 200):
        self.body, self.status = body, status


class BrowserBackend:
    def __init__(self, state_json: str | None = None):
        self.repo, self.store = MemoryRepo.load(state_json or None)
        self.dirty = False
        self.routes: list[tuple[str, re.Pattern, Callable]] = []
        self._register()

    # ------------------------------------------------------------------ public entry points
    def handle(self, method: str, path: str, body_json: str | None = None, file_name: str | None = None, file_type: str | None = None, file_bytes=None) -> str:
        parts = urlsplit(path)
        route_path = parts.path.rstrip("/") or "/"
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        body = json.loads(body_json) if body_json not in (None, "", "null") else None
        data = _to_bytes(file_bytes)
        try:
            for m, pattern, fn in self.routes:
                if m != method.upper():
                    continue
                match = pattern.fullmatch(route_path)
                if match:
                    res = fn(**match.groupdict(), body=body or {}, query=query, file=(file_name, file_type, data) if data is not None else None)
                    if method.upper() != "GET":
                        self.dirty = True
                    status = res.status if isinstance(res, Response) else 200
                    payload = res.body if isinstance(res, Response) else res
                    return json.dumps({"status": status, "body": payload}, default=str)
            return json.dumps({"status": 404, "body": {"detail": f"No route for {method} {route_path}"}})
        except ServiceError as exc:
            return json.dumps({"status": exc.status, "body": {"detail": exc.detail}})
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"status": 500, "body": {"detail": f"{exc.__class__.__name__}: {exc}", "trace": traceback.format_exc()[-1500:]}})

    def export_state(self) -> str:
        self.dirty = False
        return self.repo.dump(self.store)

    # ------------------------------------------------------------------ helpers
    def _case(self, case_id: str) -> TaxCase:
        case = self.repo.get(case_id)
        if case is None:
            raise ServiceError(404, "Case not found")
        return case

    def _commit(self, case: TaxCase, actor: str, action: str, summary: str, details: dict | None = None, evidence: list[dict] | None = None) -> None:
        case_ops.commit(self.repo, case, LOCAL_USER_ID, actor, action, summary, details, evidence)

    def _route(self, method: str, pattern: str):
        def deco(fn):
            self.routes.append((method, re.compile(pattern), fn))
            return fn
        return deco

    # ------------------------------------------------------------------ routes
    def _register(self) -> None:
        r = self._route
        repo, store = self.repo, self.store
        from app.core import audit

        @r("GET", r"/health")
        def health(**_):
            return {"status": "ok", "llm": False, "demo_mode": True, "dev_mode": True, "env": "browser"}

        @r("GET", r"/auth/me")
        def me(**_):
            return USER

        @r("POST", r"/auth/(demo|login|register)")
        def login(**_):
            return USER

        @r("POST", r"/auth/logout")
        def logout(**_):
            return {"ok": True}

        # ---- cases ---------------------------------------------------------------------
        @r("GET", r"/cases")
        def list_cases(**_):
            return {"cases": repo.list_for_user(LOCAL_USER_ID), "supported_years": supported_years(), "current_year": CURRENT_ASSESSMENT_YEAR}

        @r("POST", r"/cases")
        def create_case(body, **_):
            ay = body.get("assessment_year") or CURRENT_ASSESSMENT_YEAR
            try:
                get_rules(ay)
            except ValueError as exc:
                raise ServiceError(400, str(exc)) from exc
            case = TaxCase(owner_user_id=LOCAL_USER_ID, label=body.get("label") or f"Tax return · AY {ay}")
            case.taxpayer.assessment_year = ay
            repo.create(case)
            audit.record(repo, case.id, LOCAL_USER_ID, audit.ACTOR_USER, "case.created", f"Return workspace created for AY {ay}")
            return Response(case_view(case), 201)

        @r("GET", r"/cases/(?P<case_id>[^/]+)")
        def get_case(case_id, **_):
            return case_view(self._case(case_id))

        @r("DELETE", r"/cases/(?P<case_id>[^/]+)")
        def delete_case(case_id, **_):
            keys = repo.delete(case_id)
            for k in keys:
                store.delete(k)
            return {"deleted": True, "documents_purged": len(keys)}

        @r("GET", r"/cases/(?P<case_id>[^/]+)/dashboard")
        def dashboard(case_id, **_):
            case = self._case(case_id)
            return dashboard_service.dashboard(case, repo.audit_trail(case.id, limit=8))

        @r("GET", r"/cases/(?P<case_id>[^/]+)/onboarding")
        def onboarding(case_id, **_):
            case = self._case(case_id)
            return {**next_onboarding_step(case), "profile": profile_view(case)}

        @r("PATCH", r"/cases/(?P<case_id>[^/]+)/profile")
        def patch_profile(case_id, body, **_):
            case = self._case(case_id)
            values = {k: v for k, v in (body.get("values") or {}).items() if k in ALLOWED_PROFILE_FIELDS}
            if "assessment_year" in values:
                try:
                    get_rules(values["assessment_year"])
                except ValueError as exc:
                    raise ServiceError(400, str(exc)) from exc
            if values.get("pan"):
                pan = str(values["pan"]).strip().upper()
                if len(pan) != 10 or not (pan[:5].isalpha() and pan[5:9].isdigit() and pan[9].isalpha()):
                    raise ServiceError(400, "PAN must look like ABCDE1234F")
                values["pan"] = pan
            try:
                updated = case.taxpayer.model_copy(update=values)
                case.taxpayer = type(case.taxpayer).model_validate(updated.model_dump())
            except Exception as exc:  # noqa: BLE001
                raise ServiceError(422, f"Invalid profile values: {exc}") from exc
            confirmed = bool(body.get("confirmed", False))
            for k in values:
                case.taxpayer.field_status[k] = ValueStatus.USER_CONFIRMED if confirmed else ValueStatus.USER_ENTERED
            if next_onboarding_step(case)["completed"]:
                case.taxpayer.onboarding_completed = True
            self._commit(case, audit.ACTOR_USER, "profile.updated", f"Profile updated ({', '.join(sorted(values))})", {"fields": sorted(values)})
            return {**next_onboarding_step(case), "profile": profile_view(case)}

        @r("GET", r"/cases/(?P<case_id>[^/]+)/requirements")
        def requirements(case_id, **_):
            return {"items": [x.as_dict() for x in requirements_for(self._case(case_id))]}

        @r("POST", r"/cases/(?P<case_id>[^/]+)/entities")
        def add_entity(case_id, body, **_):
            case = self._case(case_id)
            head = str(body.get("head", "")).upper()
            if head not in case_ops.HEADS:
                raise ServiceError(400, f"Unknown head {head}")
            try:
                entity = case_ops.build_entity(head, body.get("payload") or {}, ValueStatus.USER_CONFIRMED if body.get("confirmed", True) else ValueStatus.USER_ENTERED)
            except (KeyError, ValueError, TypeError) as exc:
                raise ServiceError(422, f"Invalid payload: {exc}") from exc
            case_ops.collection_for(case, head).append(entity)
            if head in ("SALARY", "INTEREST", "DIVIDEND", "RENTAL", "CAPITAL_GAINS", "BUSINESS", "OTHER") and head not in case.taxpayer.income_sources:
                case.taxpayer.income_sources.append(head)
            self._commit(case, audit.ACTOR_USER, "entity.added", f"{head.replace('_', ' ').title()} entry added by taxpayer", {"head": head, "entity_id": entity.id})
            return Response({"entity": entity.model_dump(mode="json"), "case": case_view(case)}, 201)

        @r("PATCH", r"/cases/(?P<case_id>[^/]+)/entities/(?P<entity_id>[^/]+)")
        def patch_entity(case_id, entity_id, body, **_):
            case = self._case(case_id)
            if not case_ops.update_entity(case, entity_id, body.get("changes") or {}):
                raise ServiceError(404, "Entity not found")
            self._commit(case, audit.ACTOR_USER, "entity.updated", "Entry edited and confirmed by taxpayer", {"entity_id": entity_id})
            return {"entity": case.find_entity(entity_id).model_dump(mode="json"), "case": case_view(case)}

        @r("POST", r"/cases/(?P<case_id>[^/]+)/entities/(?P<entity_id>[^/]+)/confirm")
        def confirm_entity(case_id, entity_id, **_):
            case = self._case(case_id)
            if not case_ops.confirm_entity(case, entity_id):
                raise ServiceError(404, "Entity not found")
            self._commit(case, audit.ACTOR_USER, "entity.confirmed", "User confirmed an extracted value", {"entity_id": entity_id})
            return {"entity": case.find_entity(entity_id).model_dump(mode="json"), "case": case_view(case)}

        @r("DELETE", r"/cases/(?P<case_id>[^/]+)/entities/(?P<entity_id>[^/]+)")
        def delete_entity(case_id, entity_id, **_):
            case = self._case(case_id)
            if not case_ops.remove_entity(case, entity_id):
                raise ServiceError(404, "Entity not found")
            self._commit(case, audit.ACTOR_USER, "entity.removed", "Entry removed by taxpayer", {"entity_id": entity_id})
            return {"case": case_view(case)}

        # ---- documents -----------------------------------------------------------------------
        @r("GET", r"/cases/(?P<case_id>[^/]+)/documents")
        def list_documents(case_id, **_):
            case = self._case(case_id)
            return {"documents": [doc_view(d) for d in case.documents], "types": document_service.document_types()}

        @r("POST", r"/cases/(?P<case_id>[^/]+)/documents")
        def upload(case_id, body, file, **_):
            case = self._case(case_id)
            if not file:
                raise ServiceError(400, "No file received")
            name, ctype, data = file
            doc = document_service.upload(case, name or "document", ctype or "", data, (body or {}).get("document_type"), store, 15, allow_llm=False)
            audit.record(repo, case.id, LOCAL_USER_ID, audit.ACTOR_USER, "document.uploaded", f"{doc.type.value} uploaded ({doc.filename})", {"document_id": doc.id, "size": doc.size_bytes})
            audit.record(repo, case.id, None, audit.ACTOR_ASTRA, "document.extracted", document_service.extraction_summary(doc),
                         {"document_id": doc.id, "method": doc.extraction_method, "confidence": doc.extraction_confidence, "flags": doc.flags}, [{"label": doc.filename, "document_id": doc.id}])
            self._commit(case, audit.ACTOR_SYSTEM, "document.attached", f"Model updated from {doc.filename}", {"document_id": doc.id, "entities": doc.linked_entity_ids})
            return Response({"document": doc_view(doc), "case": case_view(case)}, 201)

        @r("GET", r"/cases/(?P<case_id>[^/]+)/documents/(?P<document_id>[^/]+)")
        def get_document(case_id, document_id, **_):
            return document_service.document_detail(self._case(case_id), document_id)

        @r("POST", r"/cases/(?P<case_id>[^/]+)/documents/(?P<document_id>[^/]+)/reprocess")
        def reprocess(case_id, document_id, query, **_):
            case = self._case(case_id)
            doc = document_service.reprocess(case, document_id, query.get("document_type"), store, allow_llm=False)
            self._commit(case, audit.ACTOR_ASTRA, "document.reprocessed", f"{doc.filename} re-processed as {doc.type.value}", {"document_id": doc.id})
            return {"document": doc_view(doc), "case": case_view(case)}

        @r("DELETE", r"/cases/(?P<case_id>[^/]+)/documents/(?P<document_id>[^/]+)")
        def delete_document(case_id, document_id, **_):
            case = self._case(case_id)
            doc = document_service.delete_document(case, document_id, store)
            self._commit(case, audit.ACTOR_USER, "document.deleted", f"{doc.filename} deleted (and its extracted values removed)", {"document_id": document_id})
            return {"case": case_view(case)}

        # ---- reconciliation / issues -------------------------------------------------------------
        @r("GET", r"/cases/(?P<case_id>[^/]+)/reconciliation")
        def get_reconciliation(case_id, **_):
            return recon_svc.reconciliation_view(self._case(case_id))

        @r("POST", r"/cases/(?P<case_id>[^/]+)/reconciliation/run")
        def run_reconciliation(case_id, **_):
            case = self._case(case_id)
            items = reconcile(case)
            repo.save(case)
            audit.record(repo, case.id, LOCAL_USER_ID, audit.ACTOR_ASTRA, "reconciliation.run", f"Reconciliation run – {len([i for i in items if i.status == 'OPEN'])} open item(s)")
            return {"items": [i.model_dump(mode="json") for i in items], "matrix": recon_svc.matrix(case)}

        @r("GET", r"/cases/(?P<case_id>[^/]+)/issues")
        def issues(case_id, **_):
            return recon_svc.issues_view(self._case(case_id))

        @r("GET", r"/cases/(?P<case_id>[^/]+)/reconciliation/(?P<item_id>[^/]+)")
        def get_item(case_id, item_id, **_):
            return recon_svc.item_detail(self._case(case_id), item_id)

        @r("POST", r"/cases/(?P<case_id>[^/]+)/reconciliation/(?P<item_id>[^/]+)/resolve")
        def resolve(case_id, item_id, body, **_):
            case = self._case(case_id)
            item, summary, navigate = recon_svc.resolve_item(case, item_id, body.get("action", ""), body.get("payload"), body.get("note"))
            if navigate:
                return {"navigate": navigate, "case": case_view(case)}
            self._commit(case, audit.ACTOR_USER, "reconciliation.resolved", f"{summary}: {item.title}", {"item_id": item.id, "action": body.get("action", "").upper(), "note": body.get("note")},
                         [{"label": e.label, "document_id": e.document_id} for e in item.evidence[:4]])
            current = next((i for i in case.reconciliation_items if i.id == item_id), item)
            return {"item": current.model_dump(mode="json"), "case": case_view(case)}

        @r("POST", r"/cases/(?P<case_id>[^/]+)/reconciliation/(?P<item_id>[^/]+)/reopen")
        def reopen(case_id, item_id, **_):
            case = self._case(case_id)
            item = recon_svc.reopen_item(case, item_id)
            self._commit(case, audit.ACTOR_USER, "reconciliation.reopened", f"Item reopened: {item.title}", {"item_id": item.id})
            return {"item": item.model_dump(mode="json")}

        # ---- computation / evidence / rules -------------------------------------------------------
        @r("GET", r"/cases/(?P<case_id>[^/]+)/computation")
        def computation(case_id, query, **_):
            case = self._case(case_id)
            from datetime import date
            as_of = date.fromisoformat(query["as_of"]) if query.get("as_of") else None
            comp = compute(case, as_of=as_of)
            return {**comp.model_dump(mode="json"), "working_regime": working_regime(case), "open_items": len(case.open_items())}

        @r("GET", r"/cases/(?P<case_id>[^/]+)/computation/explain/(?P<regime>OLD|NEW|old|new)/(?P<line_id>.+)")
        def explain(case_id, regime, line_id, **_):
            case = self._case(case_id)
            exp = number_explanation(case, compute(case), regime.upper(), line_id)
            if exp is None:
                raise ServiceError(404, "Line not found")
            return exp

        @r("GET", r"/cases/(?P<case_id>[^/]+)/evidence/tree")
        def evidence_tree(case_id, query, **_):
            case = self._case(case_id)
            reg = (query.get("regime") or working_regime(case)).upper()
            return {"regime": reg, "tree": income_evidence_tree(case, compute(case), reg)}

        @r("GET", r"/cases/(?P<case_id>[^/]+)/evidence/(?P<entity_id>[^/]+)")
        def evidence(case_id, entity_id, **_):
            ev = entity_evidence(self._case(case_id), entity_id)
            if ev is None:
                raise ServiceError(404, "Entity not found")
            return ev

        @r("GET", r"/rules")
        def rules(**_):
            return {"years": supported_years(), "rules": {ay: get_rules(ay).public_summary() for ay in supported_years()}}

        @r("GET", r"/rules/(?P<ay>[^/]+)")
        def rules_for_year(ay, **_):
            try:
                return get_rules(ay).public_summary()
            except ValueError as exc:
                raise ServiceError(404, str(exc)) from exc

        # ---- Astra -----------------------------------------------------------------------------------
        @r("GET", r"/cases/(?P<case_id>[^/]+)/astra")
        def astra(case_id, **_):
            case = self._case(case_id)
            return {"messages": repo.get_conversation(case.id), "suggested_questions": SUGGESTED_QUESTIONS, "llm_enabled": False, "model": None}

        @r("POST", r"/cases/(?P<case_id>[^/]+)/astra/ask")
        def astra_ask(case_id, body, **_):
            case = self._case(case_id)
            question = str(body.get("question", "")).strip()
            if len(question) < 2:
                raise ServiceError(400, "Ask a question")
            history = repo.get_conversation(case.id)
            result = ask(case, question, history=history, mode="deterministic")
            now = utcnow().isoformat()
            history += [{"role": "user", "content": question, "ts": now},
                        {"role": "assistant", "content": result["answer"], "ts": now, "mode": result["mode"], "tools": result["tools"], "evidence": result["evidence"], "grounding": result["grounding"]}]
            repo.save_conversation(case.id, history)
            audit.record(repo, case.id, LOCAL_USER_ID, audit.ACTOR_ASTRA, "astra.answered", f"Astra answered: “{question[:80]}” ({result['mode']}, {len(result['tools'])} tool call(s))",
                         {"mode": result["mode"], "tools": [t["name"] for t in result["tools"]], "unverified_amounts": result["grounding"]["unverified_amounts"]},
                         [{"label": e["label"], "document_id": e.get("document_id")} for e in result["evidence"][:6]])
            return result

        @r("DELETE", r"/cases/(?P<case_id>[^/]+)/astra")
        def astra_clear(case_id, **_):
            case = self._case(case_id)
            repo.save_conversation(case.id, [])
            audit.record(repo, case.id, LOCAL_USER_ID, audit.ACTOR_USER, "astra.cleared", "Conversation cleared")
            return {"ok": True}

        # ---- review ----------------------------------------------------------------------------------
        @r("GET", r"/cases/(?P<case_id>[^/]+)/review")
        def review(case_id, **_):
            return review_service.review_view(self._case(case_id))

        @r("POST", r"/cases/(?P<case_id>[^/]+)/review/confirm")
        def confirm(case_id, body, **_):
            case = self._case(case_id)
            package, high, score = review_service.confirm_review(case, body.get("declarations") or {}, body.get("selected_regime", ""), bool(body.get("acknowledge_open_issues", False)), store)
            repo.save(case)
            audit.record(repo, case.id, LOCAL_USER_ID, audit.ACTOR_USER, "review.confirmed", f"User confirmed the final review ({case.review.selected_regime.lower()} regime, {high} high-priority item(s) acknowledged)",
                         {"package_id": case.review.return_package_id, "regime": case.review.selected_regime, "readiness": score})
            return {"review_state": case.review.model_dump(mode="json"), "package": package, "note": review_service.CONFIRM_NOTE}

        @r("GET", r"/cases/(?P<case_id>[^/]+)/review/package")
        def package(case_id, **_):
            case = self._case(case_id)
            if not case.review.confirmed or not case.review.return_package_id:
                raise ServiceError(404, "No confirmed return package yet")
            return {"__blob__": True, "filename": f"astra-return-{case.assessment_year}.json", "content_type": "application/json", "content": store.get(review_service.package_key(case)).decode()}

        @r("POST", r"/cases/(?P<case_id>[^/]+)/review/reset")
        def reset(case_id, **_):
            case = self._case(case_id)
            review_service.reset_review(case)
            repo.save(case)
            audit.record(repo, case.id, LOCAL_USER_ID, audit.ACTOR_USER, "review.reopened", "Final review reopened for changes")
            return {"review_state": case.review.model_dump(mode="json")}

        # ---- audit / demo / evaluation ----------------------------------------------------------------
        @r("GET", r"/cases/(?P<case_id>[^/]+)/audit")
        def audit_trail(case_id, query, **_):
            case = self._case(case_id)
            return {"events": repo.audit_trail(case.id, limit=min(int(query.get("limit", 200)), 500))}

        @r("GET", r"/demo/scenarios")
        def demo_scenarios(**_):
            return {"enabled": True, "scenarios": list_scenarios()}

        @r("POST", r"/demo/generate")
        def demo_generate(body, **_):
            case, gen, items, computation, hidden = demo_service.generate_demo(body.get("scenario", ""), int(body.get("seed", 42)), body.get("assessment_year") or CURRENT_ASSESSMENT_YEAR, LOCAL_USER_ID, store)
            repo.create(case, hidden_test=hidden)
            demo_service.record_demo_timeline(repo, case, gen, items, computation, LOCAL_USER_ID)
            return Response({"case": case_view(case), "scenario": {"code": gen.scenario, "label": gen.label, "description": gen.description, "story": gen.story},
                             "open_items": len([i for i in items if i.status == "OPEN"])}, 201)

        @r("GET", r"/dev/evaluation/scenarios")
        def eval_scenarios(**_):
            return {"scenarios": list_scenarios()}

        @r("POST", r"/dev/evaluation/run")
        def eval_run(body, **_):
            asker = None
            if body.get("include_qa", True):
                def asker(case, question, computation):  # noqa: E306
                    return ask(case, question, computation=computation, mode="deterministic")
            try:
                results = evaluate_all(seed=int(body.get("seed", 42)), assessment_year=body.get("assessment_year") or CURRENT_ASSESSMENT_YEAR, scenarios=body.get("scenarios") or None, ask=asker)
            except KeyError as exc:
                raise ServiceError(400, str(exc)) from exc
            run_id = repo.save_eval_run(LOCAL_USER_ID, results)
            return {"run_id": run_id, **json.loads(json.dumps(results, default=str))}

        @r("GET", r"/dev/evaluation/runs")
        def eval_runs(**_):
            return {"runs": repo.list_eval_runs(LOCAL_USER_ID)}

        @r("GET", r"/dev/evaluation/case/(?P<case_id>[^/]+)")
        def eval_case(case_id, **_):
            case = repo.get(case_id)
            hidden = repo.get_hidden_test(case_id)
            if case is None or hidden is None:
                raise ServiceError(404, "No hidden test for this case")
            return eval_service.hidden_test_rows(case, hidden)
