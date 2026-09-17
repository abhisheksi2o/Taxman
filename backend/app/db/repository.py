"""Persistence for cases (encrypted JSON blobs), audit events and evaluation runs."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import get_encryptor
from app.db.models import AuditEvent, Case, EvalRun
from app.models.common import new_id, utcnow
from app.models.tax_model import TaxCase


class CaseRepository:
    def __init__(self, db: Session):
        self.db = db
        self.enc = get_encryptor()

    # ---- cases -------------------------------------------------------------------------
    def create(self, case: TaxCase, hidden_test: dict | None = None) -> Case:
        row = Case(
            id=case.id,
            user_id=case.owner_user_id,
            label=case.label,
            assessment_year=case.assessment_year,
            model_ciphertext=self.enc.encrypt_str(case.model_dump_json()),
            hidden_test_ciphertext=self.enc.encrypt_str(json.dumps(hidden_test)) if hidden_test else None,
            version=case.meta.version,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            select(Case).where(Case.user_id == user_id, Case.deleted_at.is_(None)).order_by(Case.updated_at.desc())
        ).scalars().all()
        out = []
        for r in rows:
            case = self._decode(r)
            out.append({
                "id": r.id, "label": r.label, "assessment_year": r.assessment_year,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "taxpayer_name": case.taxpayer.name, "demo_scenario": case.meta.demo_scenario,
                "onboarding_completed": case.taxpayer.onboarding_completed,
                "open_issues": len(case.open_items()), "documents": len(case.documents),
            })
        return out

    def get(self, case_id: str, user_id: str) -> TaxCase | None:
        row = self.db.get(Case, case_id)
        if row is None or row.user_id != user_id or row.deleted_at is not None:
            return None
        return self._decode(row)

    def get_hidden_test(self, case_id: str, user_id: str) -> dict | None:
        row = self.db.get(Case, case_id)
        if row is None or row.user_id != user_id or not row.hidden_test_ciphertext:
            return None
        return json.loads(self.enc.decrypt_str(row.hidden_test_ciphertext))

    def save(self, case: TaxCase) -> None:
        row = self.db.get(Case, case.id)
        if row is None:
            raise KeyError(case.id)
        case.touch()
        row.model_ciphertext = self.enc.encrypt_str(case.model_dump_json())
        row.label = case.label
        row.assessment_year = case.assessment_year
        row.version = case.meta.version
        row.updated_at = utcnow()
        self.db.commit()

    def delete(self, case_id: str, user_id: str) -> list[str]:
        """Hard-delete a case (minimal retention). Returns storage keys of documents to purge."""
        row = self.db.get(Case, case_id)
        if row is None or row.user_id != user_id:
            return []
        case = self._decode(row)
        keys = [d.storage_key for d in case.documents]
        self.db.delete(row)
        self.db.commit()
        return keys

    def purge_expired(self, retention_days: int) -> int:
        """Delete cases that have been inactive longer than the retention window (demo cases only by default)."""
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        rows = self.db.execute(select(Case).where(Case.updated_at < cutoff)).scalars().all()
        n = 0
        for r in rows:
            case = self._decode(r)
            if case.meta.demo_scenario:
                self.db.delete(r)
                n += 1
        self.db.commit()
        return n

    # ---- conversation --------------------------------------------------------------------
    def get_conversation(self, case_id: str) -> list[dict]:
        row = self.db.get(Case, case_id)
        if row is None or not row.conversation_ciphertext:
            return []
        return json.loads(self.enc.decrypt_str(row.conversation_ciphertext))

    def save_conversation(self, case_id: str, messages: list[dict]) -> None:
        row = self.db.get(Case, case_id)
        if row is None:
            return
        row.conversation_ciphertext = self.enc.encrypt_str(json.dumps(messages[-40:]))
        self.db.commit()

    # ---- audit -----------------------------------------------------------------------------
    def audit(self, case_id: str, user_id: str | None, actor: str, action: str, summary: str,
              details: dict | None = None, evidence: list[dict] | None = None) -> None:
        self.db.add(AuditEvent(case_id=case_id, user_id=user_id, actor=actor, action=action, summary=summary[:400],
                               details_json=json.dumps(details or {}, default=str),
                               evidence_json=json.dumps(evidence or [], default=str)))
        self.db.commit()

    def audit_trail(self, case_id: str, limit: int = 200) -> list[dict]:
        rows = self.db.execute(
            select(AuditEvent).where(AuditEvent.case_id == case_id).order_by(AuditEvent.ts.desc()).limit(limit)
        ).scalars().all()
        return [{
            "id": r.id, "ts": r.ts.isoformat(), "actor": r.actor, "action": r.action, "summary": r.summary,
            "details": json.loads(r.details_json or "{}"), "evidence": json.loads(r.evidence_json or "[]"),
        } for r in rows]

    # ---- evaluation runs --------------------------------------------------------------------
    def save_eval_run(self, user_id: str, results: dict) -> str:
        run_id = new_id("eval")
        self.db.add(EvalRun(id=run_id, user_id=user_id, results_json=json.dumps(results, default=str)))
        self.db.commit()
        return run_id

    def list_eval_runs(self, user_id: str, limit: int = 10) -> list[dict]:
        rows = self.db.execute(select(EvalRun).where(EvalRun.user_id == user_id).order_by(EvalRun.ts.desc()).limit(limit)).scalars().all()
        return [{"id": r.id, "ts": r.ts.isoformat(), "results": json.loads(r.results_json)} for r in rows]

    # ---- internals ----------------------------------------------------------------------------
    def _decode(self, row: Case) -> TaxCase:
        return TaxCase.model_validate_json(self.enc.decrypt_str(row.model_ciphertext))
