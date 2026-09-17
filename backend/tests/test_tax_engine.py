from datetime import date
from decimal import Decimal

from app.models.common import SourceType, TracedValue
from app.models.tax_model import CapitalGainTransaction, InterestIncome, SalaryIncome, TaxCase, TaxpayerProfile
from app.tax_engine.engine import compute, explain_line


def salaried(ay: str, gross: int, dob=date(1990, 5, 1), tds=0) -> TaxCase:
    c = TaxCase(owner_user_id="u1", taxpayer=TaxpayerProfile(assessment_year=ay, date_of_birth=dob, name="Test"))
    c.income.salary.append(SalaryIncome(employer_name="ABC Technologies", employer_tan="BLRA12345B",
                                        gross_salary=TracedValue.of(gross, SourceType.FORM16, document_id="doc1", reference="Part B Sl.1"),
                                        tds=TracedValue.of(tds, SourceType.FORM16)))
    return c


def test_new_regime_ay2627_marginal_relief():
    r = compute(salaried("2026-27", 1300000), as_of=date(2026, 7, 1))
    n = r.new.summary
    assert n.taxable_income == Decimal("1225000")
    assert n.tax_before_rebate == Decimal("63750")
    assert n.rebate_87a == Decimal("38750")  # marginal relief: tax capped at income above 12L
    assert n.total_tax_liability == Decimal("26000")
    assert r.rules_version == "AY2026-27/v1.0"


def test_old_regime_ay2627():
    r = compute(salaried("2026-27", 1300000), as_of=date(2026, 7, 1))
    assert r.old.summary.taxable_income == Decimal("1250000")
    assert r.old.summary.total_tax_liability == Decimal("195000")


def test_new_regime_ay2526_slabs_and_tds_credit():
    r = compute(salaried("2025-26", 1840000, tds=210000), as_of=date(2025, 7, 1))
    n = r.new.summary
    assert n.taxable_income == Decimal("1765000")
    assert n.total_tax_liability == Decimal("228280")
    assert n.tds == Decimal("210000")
    assert n.net_payable > 0


def test_zero_tax_up_to_12_75_lakh_salary_new_regime():
    r = compute(salaried("2026-27", 1275000), as_of=date(2026, 7, 1))
    assert r.new.summary.total_tax_liability == Decimal("0")


def test_senior_citizen_old_regime_80ttb_and_rebate():
    c = TaxCase(owner_user_id="u1", taxpayer=TaxpayerProfile(assessment_year="2026-27", date_of_birth=date(1960, 1, 1)))
    c.income.interest.append(InterestIncome(payer_name="Meridian Bank", kind="FIXED_DEPOSIT", amount=TracedValue.of(400000, SourceType.INTEREST_CERTIFICATE)))
    r = compute(c, as_of=date(2026, 7, 1))
    assert r.age_category == "SENIOR"
    assert r.old.summary.total_deductions == Decimal("50000")  # 80TTB
    assert r.old.summary.taxable_income == Decimal("350000")
    assert r.old.summary.total_tax_liability == Decimal("0")  # 5% of 50k = 2,500 fully rebated


def test_capital_gains_buckets_and_exemption():
    c = salaried("2026-27", 2000000)
    c.income.capital_gains.append(CapitalGainTransaction(description="INFY", acquisition_date=date(2023, 1, 10), transfer_date=date(2025, 9, 1),
                                                         sale_consideration=TracedValue.of(500000, SourceType.BROKER_STATEMENT), cost_of_acquisition=TracedValue.of(300000, SourceType.BROKER_STATEMENT)))
    c.income.capital_gains.append(CapitalGainTransaction(description="TCS", acquisition_date=date(2025, 4, 10), transfer_date=date(2025, 12, 1),
                                                         sale_consideration=TracedValue.of(150000, SourceType.BROKER_STATEMENT), cost_of_acquisition=TracedValue.of(100000, SourceType.BROKER_STATEMENT)))
    r = compute(c, as_of=date(2026, 7, 1))
    n = r.new.summary
    assert n.income_capital_gains == Decimal("250000")
    assert n.tax_on_normal_income == Decimal("185000")  # slab tax on 19.25L only – the exempt 1.25L never hits slab rates
    assert n.tax_on_special_income == Decimal("19375")  # 75k × 12.5% + 50k × 20%


def test_explanation_tree_has_sources_and_formulas():
    r = compute(salaried("2026-27", 1300000), as_of=date(2026, 7, 1))
    exp = explain_line(r, "NEW", "salary.standard_deduction")
    assert exp is not None and "16(ia)" in exp["line"]["rule_ref"]
    gross = r.new.find_line("salary").children[0].children[0]
    assert gross.sources and gross.sources[0].document_id == "doc1"


def test_unsupported_year_raises():
    import pytest

    from app.tax_engine.rules import UnsupportedAssessmentYear
    c = salaried("2026-27", 1000000)
    c.taxpayer.assessment_year = "2031-32"
    with pytest.raises(UnsupportedAssessmentYear):
        compute(c)
