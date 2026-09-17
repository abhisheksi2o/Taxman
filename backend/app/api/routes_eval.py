"""Developer-only AI evaluation endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.ai.astra import ask
from app.api.deps import developer_user, get_repo
from app.db.models import User
from app.db.repository import CaseRepository
from app.evaluation.runner import evaluate_all
from app.services.eval_service import hidden_test_rows
from app.synthetic.generator import list_scenarios
from app.tax_engine.rules import CURRENT_ASSESSMENT_YEAR

router = APIRouter(prefix="/dev/evaluation", tags=["evaluation"])


class RunRequest(BaseModel):
    seed: int = Field(default=42, ge=0, le=10_000_000)
    assessment_year: str = CURRENT_ASSESSMENT_YEAR
    scenarios: list[str] | None = None
    include_qa: bool = True
    qa_mode: str = "deterministic"  # deterministic | llm


@router.get("/scenarios")
def scenarios(user: User = Depends(developer_user)):
    return {"scenarios": list_scenarios()}


@router.post("/run")
def run(body: RunRequest, user: User = Depends(developer_user), repo: CaseRepository = Depends(get_repo)):
    asker = None
    if body.include_qa:
        def asker(case, question, computation):  # noqa: E306
            return ask(case, question, computation=computation, mode=body.qa_mode)
    try:
        results = evaluate_all(seed=body.seed, assessment_year=body.assessment_year, scenarios=body.scenarios, ask=asker)
    except KeyError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    run_id = repo.save_eval_run(user.id, results)
    return {"run_id": run_id, **results}


@router.get("/runs")
def runs(user: User = Depends(developer_user), repo: CaseRepository = Depends(get_repo)):
    return {"runs": repo.list_eval_runs(user.id)}


@router.get("/case/{case_id}")
def case_hidden_test(case_id: str, user: User = Depends(developer_user), repo: CaseRepository = Depends(get_repo)):
    """For a demo case: the hidden test scenario and whether each hidden issue is currently detected."""
    case = repo.get(case_id, user.id)
    hidden = repo.get_hidden_test(case_id, user.id)
    if case is None or hidden is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No hidden test for this case")
    return hidden_test_rows(case, hidden)
