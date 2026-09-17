"""Every hidden issue in every demo scenario must be detected with the right evidence and impact; the
clean scenario must produce no HIGH/MEDIUM items (false-positive control)."""
import pytest

from app.ai.astra import ask
from app.evaluation.runner import evaluate_all, evaluate_scenario
from app.synthetic.generator import list_scenarios


@pytest.mark.parametrize("code", [s["code"] for s in list_scenarios()])
def test_scenario_detection(code):
    res = evaluate_scenario(code, seed=42, assessment_year="2026-27", ask=lambda case, q, comp: ask(case, q, computation=comp, mode="deterministic"))
    assert res["detected"] == res["hidden_issue_count"], [h for h in res["hidden_issues"] if not h["detected"]]
    assert res["passed"] == res["hidden_issue_count"], [h for h in res["hidden_issues"] if h["result"] != "PASS"]
    assert res["false_positives"] == [], res["false_positives"]
    for q in res["qa"]:
        assert q["hallucinated_amounts"] == [], q
        assert q["mentions_ok"], q


def test_full_benchmark_summary():
    res = evaluate_all(seed=7, assessment_year="2026-27")
    assert res["detection_rate"] == 1.0
    assert res["false_positives"] == 0


def test_previous_year_rules_also_work():
    res = evaluate_scenario("missing_income", seed=3, assessment_year="2025-26")
    assert res["detected"] == 1
