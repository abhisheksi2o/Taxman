"""Assessment Year 2026-27 (Financial Year 2025-26) – Finance Act 2025.

Key features of this year:
* New-regime slabs widened (nil up to ₹4 lakh, 30% above ₹24 lakh); rebate u/s 87A up to ₹60,000 for total
  income up to ₹12 lakh with marginal relief (special-rate income excluded from the rebate).
* Standard deduction ₹75,000 (new) / ₹50,000 (old); old-regime slabs unchanged.
* Capital-gains rates as introduced from 23 July 2024 apply for the full year.
* TDS thresholds raised (bank interest ₹50,000 / ₹1,00,000 senior; dividend ₹10,000; rent ₹6,00,000 p.a.).
* The Income-tax Act, 2025 replaces the 1961 Act only from tax year 2026-27 onwards – this AY is still
  governed by the 1961 Act.
"""
from __future__ import annotations

from datetime import date

from app.models.common import AgeCategory

from .base import (
    CII,
    NEW_ALLOWED_VIA,
    NEW_SURCHARGE,
    OLD_ALLOWED_VIA,
    OLD_REGIME_SLABS,
    OLD_SURCHARGE,
    AdvanceTaxInstalment,
    AYRules,
    CapitalGainRate,
    D,
    HoldingPeriodRule,
    RebateRule,
    RegimeRules,
    Slab,
    TdsThreshold,
    instalments_for_fy,
    standard_deduction_limits,
)

NEW_SLABS_AY2627 = [
    Slab(lower=D(0), upper=D(400000), rate=D("0")),
    Slab(lower=D(400000), upper=D(800000), rate=D("0.05")),
    Slab(lower=D(800000), upper=D(1200000), rate=D("0.10")),
    Slab(lower=D(1200000), upper=D(1600000), rate=D("0.15")),
    Slab(lower=D(1600000), upper=D(2000000), rate=D("0.20")),
    Slab(lower=D(2000000), upper=D(2400000), rate=D("0.25")),
    Slab(lower=D(2400000), upper=None, rate=D("0.30")),
]

RULES = AYRules(
    assessment_year="2026-27",
    financial_year="2025-26",
    fy_start=date(2025, 4, 1),
    fy_end=date(2026, 3, 31),
    version="AY2026-27/v1.0",
    status="FINAL",
    sources=[
        "Income-tax Act, 1961 as amended by the Finance Act, 2025",
        "Section 115BAC(1A) – rates for AY 2026-27; section 87A rebate ₹60,000 with marginal relief",
        "Finance Act 2025 – revised TDS thresholds effective 1 April 2025",
        "Cost Inflation Index notification for FY 2025-26 (376)",
    ],
    due_date_individual=date(2026, 7, 31),
    due_date_note="Statutory date for non-audit individuals. Update this rule if CBDT notifies an extension.",
    cess_rate=D("0.04"),
    old=RegimeRules(
        code="OLD",
        label="Old regime (normal provisions)",
        section="Normal provisions of the Act",
        slabs=OLD_REGIME_SLABS,
        standard_deduction_salary=D(50000),
        rebate=RebateRule(income_threshold=D(500000), max_rebate=D(12500), marginal_relief=False),
        surcharge_bands=OLD_SURCHARGE,
        surcharge_cap_on_special_income=D("0.15"),
        professional_tax_deductible=True,
        hra_lta_exemption_allowed=True,
        self_occupied_interest_limit=D(200000),
        hp_loss_setoff_limit=D(200000),
        family_pension_deduction_cap=D(15000),
        chapter_via_allowed_sections=OLD_ALLOWED_VIA,
        employer_nps_rate=D("0.10"),
        notes=["Age-based basic exemption applies to resident senior / super-senior citizens.",
               "Opting out of the default new regime requires Form 10-IEA for taxpayers with business income."],
    ),
    new=RegimeRules(
        code="NEW",
        label="New regime (section 115BAC)",
        section="115BAC(1A)",
        slabs={AgeCategory.GENERAL: NEW_SLABS_AY2627, AgeCategory.SENIOR: NEW_SLABS_AY2627,
               AgeCategory.SUPER_SENIOR: NEW_SLABS_AY2627},
        standard_deduction_salary=D(75000),
        rebate=RebateRule(income_threshold=D(1200000), max_rebate=D(60000), marginal_relief=True),
        surcharge_bands=NEW_SURCHARGE,
        surcharge_cap_on_special_income=D("0.15"),
        professional_tax_deductible=False,
        hra_lta_exemption_allowed=False,
        self_occupied_interest_limit=D(0),
        hp_loss_setoff_limit=D(0),
        family_pension_deduction_cap=D(25000),
        chapter_via_allowed_sections=NEW_ALLOWED_VIA,
        employer_nps_rate=D("0.14"),
        notes=["Default regime. Rebate u/s 87A does not apply to income taxed at special rates (111A/112/112A).",
               "Surcharge capped at 25%."],
    ),
    default_regime="NEW",
    senior_age=60,
    super_senior_age=80,
    ltcg_112a_exemption=D(125000),
    capital_gain_rates=[
        CapitalGainRate(bucket="STCG_111A", effective_from=date(2025, 4, 1), rate=D("0.20"),
                        label="STCG on STT-paid equity – 20%"),
        CapitalGainRate(bucket="STCG_OTHER", effective_from=date(2025, 4, 1), rate=None,
                        label="Other short-term gains – slab rates"),
        CapitalGainRate(bucket="LTCG_112A", effective_from=date(2025, 4, 1), rate=D("0.125"),
                        label="LTCG on STT-paid equity – 12.5% above ₹1,25,000"),
        CapitalGainRate(bucket="LTCG_112", effective_from=date(2025, 4, 1), rate=D("0.125"),
                        label="LTCG on other assets – 12.5% without indexation"),
        CapitalGainRate(bucket="LTCG_112_INDEXED", effective_from=date(2025, 4, 1), rate=D("0.20"), indexation=True,
                        label="Option for land/building acquired before 23 Jul 2024 – 20% with indexation"),
    ],
    holding_periods=[
        HoldingPeriodRule(asset_classes=["LISTED_EQUITY", "EQUITY_MF", "LISTED_BOND"], months=12,
                          effective_from=date(2025, 4, 1)),
        HoldingPeriodRule(asset_classes=["UNLISTED_SHARES", "IMMOVABLE_PROPERTY", "DEBT_MF", "GOLD", "OTHER"],
                          months=24, effective_from=date(2025, 4, 1)),
    ],
    specified_mf_cutoff=date(2023, 4, 1),
    cii=CII,
    deduction_limits=standard_deduction_limits(),
    presumptive_44ada_rate=D("0.50"),
    presumptive_44ada_limit=D(5000000),
    presumptive_44ada_limit_digital=D(7500000),
    presumptive_44ad_rate_cash=D("0.08"),
    presumptive_44ad_rate_digital=D("0.06"),
    presumptive_44ad_limit=D(20000000),
    presumptive_44ad_limit_digital=D(30000000),
    interest_rate_234a=D("0.01"),
    interest_rate_234b=D("0.01"),
    interest_rate_234c=D("0.01"),
    advance_tax_threshold=D(10000),
    advance_tax_instalments=instalments_for_fy(2025),
    presumptive_instalment=AdvanceTaxInstalment(due_on=date(2026, 3, 15), cumulative_pct=D("1.00"),
                                                 interest_months=1),
    tds_thresholds={
        "194A_BANK": TdsThreshold(section="194A", label="Interest from banks / post office / co-op",
                                  threshold=D(50000), senior_threshold=D(100000), rate=D("0.10")),
        "194A_OTHER": TdsThreshold(section="194A", label="Other interest", threshold=D(10000), rate=D("0.10")),
        "194": TdsThreshold(section="194", label="Dividend", threshold=D(10000), rate=D("0.10")),
        "194H": TdsThreshold(section="194H", label="Commission / brokerage", threshold=D(20000), rate=D("0.02")),
        "194J": TdsThreshold(section="194J", label="Professional fees", threshold=D(50000), rate=D("0.10")),
        "194I": TdsThreshold(section="194-I", label="Rent (business payer)", threshold=D(600000), rate=D("0.10")),
        "194IB": TdsThreshold(section="194-IB", label="Rent by individuals (per month)", threshold=D(50000),
                              rate=D("0.02")),
    },
    itr1_income_limit=D(5000000),
    itr1_ltcg_112a_limit=D(125000),
)
