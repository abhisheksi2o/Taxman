"""Ask Astra – grounded conversational investigator."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.ai.astra import SUGGESTED_QUESTIONS, ask
from app.api.deps import current_user, get_repo, load_case
from app.config import Settings, get_settings
from app.core import audit
from app.core.security import astra_limiter
from app.db.models import User
from app.db.repository import CaseRepository
from app.models.common import utcnow
from app.models.tax_model import TaxCase

router = APIRouter(prefix="/cases/{case_id}/astra", tags=["astra"])


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    mode: str | None = None  # llm | deterministic | None (auto)


@router.get("")
def conversation(case: TaxCase = Depends(load_case), repo: CaseRepository = Depends(get_repo), settings: Settings = Depends(get_settings)):
    return {"messages": repo.get_conversation(case.id), "suggested_questions": SUGGESTED_QUESTIONS, "llm_enabled": settings.llm_available,
            "model": settings.astra_llm_model if settings.llm_available else None}


@router.post("/ask")
def ask_astra(body: Question, case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo), settings: Settings = Depends(get_settings)):
    if not astra_limiter.allow(f"astra:{user.id}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many questions – please wait a moment")
    mode = body.mode
    if mode == "llm" and not settings.llm_available:
        mode = None
    history = repo.get_conversation(case.id)
    result = ask(case, body.question, history=history, mode=mode)
    now = utcnow().isoformat()
    history += [{"role": "user", "content": body.question, "ts": now},
                {"role": "assistant", "content": result["answer"], "ts": now, "mode": result["mode"], "tools": result["tools"], "evidence": result["evidence"], "grounding": result["grounding"]}]
    repo.save_conversation(case.id, history)
    audit.record(repo, case.id, user.id, audit.ACTOR_ASTRA, "astra.answered", f"Astra answered: “{body.question[:80]}” ({result['mode']}, {len(result['tools'])} tool call(s))",
                 {"mode": result["mode"], "tools": [t["name"] for t in result["tools"]], "unverified_amounts": result["grounding"]["unverified_amounts"]},
                 [{"label": e["label"], "document_id": e.get("document_id")} for e in result["evidence"][:6]])
    return result


@router.delete("")
def clear_conversation(case: TaxCase = Depends(load_case), user: User = Depends(current_user), repo: CaseRepository = Depends(get_repo)):
    repo.save_conversation(case.id, [])
    audit.record(repo, case.id, user.id, audit.ACTOR_USER, "astra.cleared", "Conversation cleared")
    return {"ok": True}
