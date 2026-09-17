"""Audit trail helpers – thin wrappers so call sites read clearly."""
from __future__ import annotations

from app.db.repository import CaseRepository

ACTOR_USER = "USER"
ACTOR_SYSTEM = "SYSTEM"
ACTOR_ASTRA = "ASTRA"


def record(repo: CaseRepository, case_id: str, user_id: str | None, actor: str, action: str, summary: str,
           details: dict | None = None, evidence: list[dict] | None = None) -> None:
    repo.audit(case_id, user_id, actor, action, summary, details, evidence)
