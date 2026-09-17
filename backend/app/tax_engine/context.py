"""Taxpayer context derived deterministically from the profile (age category, residency, dates)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.models.common import AgeCategory, ResidentialStatus
from app.models.tax_model import TaxCase
from app.tax_engine.rules.base import AYRules


@dataclass(frozen=True)
class TaxContext:
    assessment_year: str
    age_category: AgeCategory
    residential_status: ResidentialStatus
    is_resident: bool
    is_senior: bool
    has_business_income: bool
    has_presumptive_only: bool
    fy_start: date
    fy_end: date
    as_of: date


def age_category_for(dob: date | None, rules: AYRules) -> AgeCategory:
    """A person who turns 60 (or 80) at any time during the FY – including on 1 April following it –
    is treated as senior / super-senior (CBDT Circular 28/2016)."""
    if dob is None:
        return AgeCategory.GENERAL
    cutoff_senior = date(rules.fy_end.year - rules.senior_age, 4, 1)
    cutoff_super = date(rules.fy_end.year - rules.super_senior_age, 4, 1)
    if dob <= cutoff_super:
        return AgeCategory.SUPER_SENIOR
    if dob <= cutoff_senior:
        return AgeCategory.SENIOR
    return AgeCategory.GENERAL


def build_context(case: TaxCase, rules: AYRules, as_of: date | None = None) -> TaxContext:
    tp = case.taxpayer
    age = age_category_for(tp.date_of_birth, rules)
    is_resident = tp.residential_status in (ResidentialStatus.RESIDENT, ResidentialStatus.RNOR)
    # age-based exemption limits apply only to residents
    if not is_resident:
        age = AgeCategory.GENERAL
    business = case.income.business
    has_business = bool(business)
    presumptive_only = bool(business) and all(b.nature in ("PROFESSION_44ADA", "BUSINESS_44AD") for b in business)
    return TaxContext(
        assessment_year=rules.assessment_year,
        age_category=age,
        residential_status=tp.residential_status,
        is_resident=is_resident,
        is_senior=age in (AgeCategory.SENIOR, AgeCategory.SUPER_SENIOR),
        has_business_income=has_business,
        has_presumptive_only=presumptive_only,
        fy_start=rules.fy_start,
        fy_end=rules.fy_end,
        as_of=as_of or date.today(),
    )
