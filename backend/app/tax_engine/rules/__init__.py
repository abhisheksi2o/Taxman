"""Registry of versioned rule sets keyed by Assessment Year."""
from __future__ import annotations

from .ay_2025_26 import RULES as AY_2025_26
from .ay_2026_27 import RULES as AY_2026_27
from .base import AYRules

RULE_SETS: dict[str, AYRules] = {
    AY_2025_26.assessment_year: AY_2025_26,
    AY_2026_27.assessment_year: AY_2026_27,
}

CURRENT_ASSESSMENT_YEAR = "2026-27"


class UnsupportedAssessmentYear(ValueError):
    pass


def get_rules(assessment_year: str) -> AYRules:
    try:
        return RULE_SETS[assessment_year]
    except KeyError as exc:
        raise UnsupportedAssessmentYear(
            f"No rule set is loaded for AY {assessment_year}. Supported: {sorted(RULE_SETS)}"
        ) from exc


def supported_years() -> list[str]:
    return sorted(RULE_SETS)


__all__ = ["AYRules", "RULE_SETS", "CURRENT_ASSESSMENT_YEAR", "get_rules", "supported_years",
           "UnsupportedAssessmentYear"]
