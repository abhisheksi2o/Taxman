"""Rule-set schema. Every number the engine uses lives in an AY-specific rule module, never in code.

Rule modules are data: slabs, thresholds, rates, dates and the official sources they were taken from.
The engine is generic and reads whichever rule set is registered for the case's Assessment Year.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.common import AgeCategory, Money


class Slab(BaseModel):
    model_config = ConfigDict(frozen=True)
    lower: Money
    upper: Money | None  # None = no upper bound
    rate: Money  # e.g. 0.05


class SurchargeBand(BaseModel):
    model_config = ConfigDict(frozen=True)
    above: Money
    rate: Money


class RebateRule(BaseModel):
    model_config = ConfigDict(frozen=True)
    section: str = "87A"
    income_threshold: Money
    max_rebate: Money
    marginal_relief: bool  # tax cannot exceed income above the threshold
    applies_to_special_rate_income: bool = False


class RegimeRules(BaseModel):
    model_config = ConfigDict(frozen=True)
    code: Literal["OLD", "NEW"]
    label: str
    section: str  # "Normal provisions" / "115BAC"
    slabs: dict[AgeCategory, list[Slab]]
    standard_deduction_salary: Money
    rebate: RebateRule
    surcharge_bands: list[SurchargeBand]
    surcharge_cap_on_special_income: Money  # 0.15 on dividends / 111A / 112 / 112A
    professional_tax_deductible: bool
    hra_lta_exemption_allowed: bool
    self_occupied_interest_limit: Money  # 24(b) on self-occupied property
    hp_loss_setoff_limit: Money  # against other heads (71(3A))
    family_pension_deduction_cap: Money
    chapter_via_allowed_sections: list[str]  # deductions the regime allows
    employer_nps_rate: Money  # 80CCD(2) as % of salary (basic + DA)
    notes: list[str] = Field(default_factory=list)


class CapitalGainRate(BaseModel):
    model_config = ConfigDict(frozen=True)
    bucket: Literal["STCG_111A", "STCG_OTHER", "LTCG_112A", "LTCG_112", "LTCG_112_INDEXED"]
    effective_from: date
    rate: Money | None  # None = taxed at slab rates
    indexation: bool = False
    label: str


class HoldingPeriodRule(BaseModel):
    model_config = ConfigDict(frozen=True)
    asset_classes: list[str]
    months: int
    effective_from: date


class AdvanceTaxInstalment(BaseModel):
    model_config = ConfigDict(frozen=True)
    due_on: date
    cumulative_pct: Money
    interest_months: int


class DeductionLimit(BaseModel):
    model_config = ConfigDict(frozen=True)
    section: str
    label: str
    limit: Money | None  # None = no monetary cap
    senior_limit: Money | None = None
    notes: str | None = None


class TdsThreshold(BaseModel):
    model_config = ConfigDict(frozen=True)
    section: str
    label: str
    threshold: Money
    senior_threshold: Money | None = None
    rate: Money


class AYRules(BaseModel):
    model_config = ConfigDict(frozen=True)

    assessment_year: str
    financial_year: str
    fy_start: date
    fy_end: date
    version: str
    status: Literal["FINAL", "PROVISIONAL"]
    sources: list[str]
    due_date_individual: date
    due_date_note: str | None = None
    cess_rate: Money
    old: RegimeRules
    new: RegimeRules
    default_regime: Literal["OLD", "NEW"]
    senior_age: int
    super_senior_age: int
    ltcg_112a_exemption: Money
    capital_gain_rates: list[CapitalGainRate]
    holding_periods: list[HoldingPeriodRule]
    specified_mf_cutoff: date  # section 50AA – debt MF units acquired on/after are always short-term
    cii: dict[str, int]
    deduction_limits: dict[str, DeductionLimit]
    presumptive_44ada_rate: Money
    presumptive_44ada_limit: Money
    presumptive_44ada_limit_digital: Money
    presumptive_44ad_rate_cash: Money
    presumptive_44ad_rate_digital: Money
    presumptive_44ad_limit: Money
    presumptive_44ad_limit_digital: Money
    interest_rate_234a: Money
    interest_rate_234b: Money
    interest_rate_234c: Money
    advance_tax_threshold: Money
    advance_tax_instalments: list[AdvanceTaxInstalment]
    presumptive_instalment: AdvanceTaxInstalment
    tds_thresholds: dict[str, TdsThreshold]
    itr1_income_limit: Money
    itr1_ltcg_112a_limit: Money
    rounding_total_income: Money = Decimal("10")
    rounding_tax: Money = Decimal("10")

    def regime(self, code: str) -> RegimeRules:
        return self.old if code == "OLD" else self.new

    def basic_exemption(self, regime: str, age: AgeCategory) -> Decimal:
        slabs = self.regime(regime).slabs.get(age) or self.regime(regime).slabs[AgeCategory.GENERAL]
        return Decimal(slabs[0].upper or 0)

    def cg_rate(self, bucket: str, transfer_date: date) -> CapitalGainRate:
        candidates = [r for r in self.capital_gain_rates if r.bucket == bucket and r.effective_from <= transfer_date]
        if not candidates:
            candidates = [r for r in self.capital_gain_rates if r.bucket == bucket]
        return sorted(candidates, key=lambda r: r.effective_from)[-1]

    def holding_months(self, asset_class: str, transfer_date: date) -> int:
        candidates = [
            h for h in self.holding_periods if asset_class in h.asset_classes and h.effective_from <= transfer_date
        ]
        if not candidates:
            candidates = [h for h in self.holding_periods if asset_class in h.asset_classes]
        if not candidates:
            return 24
        return sorted(candidates, key=lambda h: h.effective_from)[-1].months

    def public_summary(self) -> dict:
        return {
            "assessment_year": self.assessment_year,
            "financial_year": self.financial_year,
            "version": self.version,
            "status": self.status,
            "due_date_individual": self.due_date_individual.isoformat(),
            "due_date_note": self.due_date_note,
            "sources": self.sources,
            "cess_rate": float(self.cess_rate),
            "default_regime": self.default_regime,
            "regimes": {
                code: {
                    "label": r.label,
                    "section": r.section,
                    "standard_deduction_salary": float(r.standard_deduction_salary),
                    "rebate": {
                        "income_threshold": float(r.rebate.income_threshold),
                        "max_rebate": float(r.rebate.max_rebate),
                        "marginal_relief": r.rebate.marginal_relief,
                    },
                    "slabs": {
                        age.value: [
                            {"lower": float(s.lower), "upper": float(s.upper) if s.upper is not None else None,
                             "rate": float(s.rate)}
                            for s in slabs
                        ]
                        for age, slabs in r.slabs.items()
                    },
                    "surcharge": [{"above": float(b.above), "rate": float(b.rate)} for b in r.surcharge_bands],
                    "chapter_via_allowed_sections": r.chapter_via_allowed_sections,
                    "notes": r.notes,
                }
                for code, r in (("OLD", self.old), ("NEW", self.new))
            },
            "ltcg_112a_exemption": float(self.ltcg_112a_exemption),
            "capital_gain_rates": [
                {"bucket": r.bucket, "effective_from": r.effective_from.isoformat(),
                 "rate": float(r.rate) if r.rate is not None else None, "indexation": r.indexation, "label": r.label}
                for r in self.capital_gain_rates
            ],
            "deduction_limits": {
                k: {"label": v.label, "limit": float(v.limit) if v.limit is not None else None,
                    "senior_limit": float(v.senior_limit) if v.senior_limit is not None else None, "notes": v.notes}
                for k, v in self.deduction_limits.items()
            },
            "tds_thresholds": {
                k: {"label": v.label, "threshold": float(v.threshold),
                    "senior_threshold": float(v.senior_threshold) if v.senior_threshold is not None else None,
                    "rate": float(v.rate)}
                for k, v in self.tds_thresholds.items()
            },
            "interest": {"234A": float(self.interest_rate_234a), "234B": float(self.interest_rate_234b),
                         "234C": float(self.interest_rate_234c)},
        }


# ---- shared building blocks (values that did not change between the rule sets) -------------

def D(v: str | int | float) -> Decimal:
    return Decimal(str(v))


OLD_REGIME_SLABS: dict[AgeCategory, list[Slab]] = {
    AgeCategory.GENERAL: [
        Slab(lower=D(0), upper=D(250000), rate=D("0")),
        Slab(lower=D(250000), upper=D(500000), rate=D("0.05")),
        Slab(lower=D(500000), upper=D(1000000), rate=D("0.20")),
        Slab(lower=D(1000000), upper=None, rate=D("0.30")),
    ],
    AgeCategory.SENIOR: [
        Slab(lower=D(0), upper=D(300000), rate=D("0")),
        Slab(lower=D(300000), upper=D(500000), rate=D("0.05")),
        Slab(lower=D(500000), upper=D(1000000), rate=D("0.20")),
        Slab(lower=D(1000000), upper=None, rate=D("0.30")),
    ],
    AgeCategory.SUPER_SENIOR: [
        Slab(lower=D(0), upper=D(500000), rate=D("0")),
        Slab(lower=D(500000), upper=D(1000000), rate=D("0.20")),
        Slab(lower=D(1000000), upper=None, rate=D("0.30")),
    ],
}

OLD_SURCHARGE = [
    SurchargeBand(above=D(5000000), rate=D("0.10")),
    SurchargeBand(above=D(10000000), rate=D("0.15")),
    SurchargeBand(above=D(20000000), rate=D("0.25")),
    SurchargeBand(above=D(50000000), rate=D("0.37")),
]
NEW_SURCHARGE = [
    SurchargeBand(above=D(5000000), rate=D("0.10")),
    SurchargeBand(above=D(10000000), rate=D("0.15")),
    SurchargeBand(above=D(20000000), rate=D("0.25")),
]

OLD_ALLOWED_VIA = [
    "80C", "80CCC", "80CCD1", "80CCD1B", "80CCD2", "80D", "80DD", "80DDB", "80E", "80EE", "80EEA", "80EEB",
    "80G", "80GG", "80GGA", "80GGC", "80TTA", "80TTB", "80U",
]
NEW_ALLOWED_VIA = ["80CCD2"]

CII: dict[str, int] = {
    "2001-02": 100, "2002-03": 105, "2003-04": 109, "2004-05": 113, "2005-06": 117, "2006-07": 122,
    "2007-08": 129, "2008-09": 137, "2009-10": 148, "2010-11": 167, "2011-12": 184, "2012-13": 200,
    "2013-14": 220, "2014-15": 240, "2015-16": 254, "2016-17": 264, "2017-18": 272, "2018-19": 280,
    "2019-20": 289, "2020-21": 301, "2021-22": 317, "2022-23": 331, "2023-24": 348, "2024-25": 363,
    "2025-26": 376,
}


def standard_deduction_limits(senior_80ttb: bool = True) -> dict[str, DeductionLimit]:
    return {
        "80C": DeductionLimit(section="80C", label="Life insurance, PPF, ELSS, EPF, tuition fees, home-loan principal…",
                              limit=D(150000), notes="Aggregate cap with 80CCC and 80CCD(1) under 80CCE"),
        "80CCC": DeductionLimit(section="80CCC", label="Pension fund contributions", limit=D(150000)),
        "80CCD1": DeductionLimit(section="80CCD1", label="Own NPS contribution", limit=D(150000)),
        "80CCD1B": DeductionLimit(section="80CCD1B", label="Additional NPS contribution", limit=D(50000)),
        "80CCD2": DeductionLimit(section="80CCD2", label="Employer NPS contribution", limit=None,
                                 notes="Capped at 10%/14% of salary depending on regime"),
        "80D": DeductionLimit(section="80D", label="Health insurance – self & family", limit=D(25000),
                              senior_limit=D(50000), notes="Parents bucket adds ₹25,000 (₹50,000 if senior)"),
        "80DD": DeductionLimit(section="80DD", label="Dependent with disability", limit=D(75000),
                               senior_limit=None, notes="₹1,25,000 for severe disability"),
        "80DDB": DeductionLimit(section="80DDB", label="Specified medical treatment", limit=D(40000),
                                senior_limit=D(100000)),
        "80E": DeductionLimit(section="80E", label="Education loan interest", limit=None),
        "80EE": DeductionLimit(section="80EE", label="Home-loan interest (first-time buyers, 2016-17 loans)",
                               limit=D(50000)),
        "80EEA": DeductionLimit(section="80EEA", label="Affordable-housing loan interest", limit=D(150000)),
        "80EEB": DeductionLimit(section="80EEB", label="Electric-vehicle loan interest", limit=D(150000)),
        "80G": DeductionLimit(section="80G", label="Donations", limit=None,
                              notes="50%/100% of the donation; some donations limited to 10% of adjusted GTI"),
        "80GG": DeductionLimit(section="80GG", label="Rent paid (no HRA)", limit=D(60000)),
        "80GGA": DeductionLimit(section="80GGA", label="Donations for scientific research", limit=None),
        "80GGC": DeductionLimit(section="80GGC", label="Contributions to political parties", limit=None),
        "80TTA": DeductionLimit(section="80TTA", label="Savings-account interest", limit=D(10000),
                                notes="Not for senior citizens (80TTB applies instead)"),
        "80TTB": DeductionLimit(section="80TTB", label="Deposit interest – senior citizens", limit=D(50000)),
        "80U": DeductionLimit(section="80U", label="Taxpayer with disability", limit=D(75000),
                              notes="₹1,25,000 for severe disability"),
    }


STANDARD_INSTALMENTS = [
    AdvanceTaxInstalment(due_on=date(2000, 6, 15), cumulative_pct=D("0.15"), interest_months=3),
    AdvanceTaxInstalment(due_on=date(2000, 9, 15), cumulative_pct=D("0.45"), interest_months=3),
    AdvanceTaxInstalment(due_on=date(2000, 12, 15), cumulative_pct=D("0.75"), interest_months=3),
    AdvanceTaxInstalment(due_on=date(2001, 3, 15), cumulative_pct=D("1.00"), interest_months=1),
]


def instalments_for_fy(fy_start_year: int) -> list[AdvanceTaxInstalment]:
    out = []
    for i in STANDARD_INSTALMENTS:
        year = fy_start_year if i.due_on.month > 3 else fy_start_year + 1
        out.append(AdvanceTaxInstalment(due_on=date(year, i.due_on.month, i.due_on.day),
                                        cumulative_pct=i.cumulative_pct, interest_months=i.interest_months))
    return out
