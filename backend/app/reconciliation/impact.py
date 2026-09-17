"""Deterministic tax-impact estimates for reconciliation items.

An impact is the change in *total tax liability* (or in creditable TDS) produced by applying a specific
resolution to a deep copy of the case, computed by the tax engine under the taxpayer's working regime.
The same helpers are used by the demo generator to compute the *correct* impact for hidden test issues,
so evaluation compares like with like.
"""
from __future__ import annotations

import copy
from collections.abc import Callable
from decimal import Decimal

from app.models.common import SourceType, TracedValue, ValueStatus, money
from app.models.tax_model import BusinessIncome, DividendIncome, InterestIncome, RentalIncome, SalaryIncome, TaxCase
from app.tax_engine.engine import compute


def working_regime(case: TaxCase) -> str:
    if case.review.selected_regime:
        return case.review.selected_regime
    if case.taxpayer.regime_preference in ("OLD", "NEW"):
        return case.taxpayer.regime_preference
    return "NEW"


def tax_delta(case: TaxCase, mutate: Callable[[TaxCase], None]) -> Decimal:
    """Liability after applying ``mutate`` minus liability before (positive = more tax)."""
    regime = working_regime(case)
    before = compute(case).regime(regime).summary.total_tax_liability
    clone = copy.deepcopy(case)
    mutate(clone)
    after = compute(clone).regime(regime).summary.total_tax_liability
    return money(after - before)


# ---- reusable mutations -----------------------------------------------------------------------

def add_interest(payer: str, amount, kind: str = "SAVINGS", tds=0, account_ref: str | None = None,
                 source: SourceType = SourceType.USER_INPUT, document_id: str | None = None, reference: str | None = None) -> Callable[[TaxCase], None]:
    def _m(c: TaxCase) -> None:
        c.income.interest.append(InterestIncome(payer_name=payer, kind=kind, account_ref=account_ref,
                                                amount=TracedValue.of(amount, source, status=ValueStatus.USER_CONFIRMED, document_id=document_id, reference=reference),
                                                tds=TracedValue.of(tds or 0, source, status=ValueStatus.USER_CONFIRMED), status=ValueStatus.USER_CONFIRMED))
    return _m


def add_salary(employer: str, tan: str | None, gross, tds=0, exempt=0, professional_tax=0) -> Callable[[TaxCase], None]:
    def _m(c: TaxCase) -> None:
        c.income.salary.append(SalaryIncome(employer_name=employer, employer_tan=tan, gross_salary=TracedValue.of(gross, SourceType.USER_INPUT, status=ValueStatus.USER_CONFIRMED),
                                            exempt_allowances=TracedValue.of(exempt, SourceType.USER_INPUT), professional_tax=TracedValue.of(professional_tax, SourceType.USER_INPUT),
                                            tds=TracedValue.of(tds, SourceType.USER_INPUT), status=ValueStatus.USER_CONFIRMED))
    return _m


def add_dividend(payer: str, amount, tds=0) -> Callable[[TaxCase], None]:
    def _m(c: TaxCase) -> None:
        c.income.dividend.append(DividendIncome(payer_name=payer, amount=TracedValue.of(amount, SourceType.USER_INPUT, status=ValueStatus.USER_CONFIRMED),
                                                tds=TracedValue.of(tds, SourceType.USER_INPUT), status=ValueStatus.USER_CONFIRMED))
    return _m


def add_rental(name: str, annual_rent, municipal_tax=0, tenant_tds=0, tenant: str | None = None) -> Callable[[TaxCase], None]:
    def _m(c: TaxCase) -> None:
        c.income.rental.append(RentalIncome(property_name=name, tenant_name=tenant, annual_rent_received=TracedValue.of(annual_rent, SourceType.USER_INPUT, status=ValueStatus.USER_CONFIRMED),
                                            municipal_taxes_paid=TracedValue.of(municipal_tax, SourceType.USER_INPUT), tenant_tds=TracedValue.of(tenant_tds, SourceType.USER_INPUT),
                                            status=ValueStatus.USER_CONFIRMED))
    return _m


def add_business_receipts(amount, description: str = "Professional receipts") -> Callable[[TaxCase], None]:
    def _m(c: TaxCase) -> None:
        if c.income.business:
            b = c.income.business[0]
            b.gross_receipts = TracedValue.of(money(b.gross_receipts.amount) + money(amount), SourceType.USER_INPUT, status=ValueStatus.USER_CONFIRMED)
        else:
            c.income.business.append(BusinessIncome(description=description, nature="PROFESSION_44ADA", gross_receipts=TracedValue.of(amount, SourceType.USER_INPUT), status=ValueStatus.USER_CONFIRMED))
    return _m


def remove_entity(entity_id: str) -> Callable[[TaxCase], None]:
    def _m(c: TaxCase) -> None:
        for coll in (c.income.interest, c.income.dividend, c.income.capital_gains, c.income.salary, c.income.rental, c.income.other_sources, c.income.business):
            for e in list(coll):
                if e.id == entity_id:
                    coll.remove(e)
    return _m


def adjust_interest_amount(entity_id: str, new_amount) -> Callable[[TaxCase], None]:
    def _m(c: TaxCase) -> None:
        for i in c.income.interest:
            if i.id == entity_id:
                i.amount = TracedValue.of(new_amount, SourceType.USER_INPUT, status=ValueStatus.USER_CONFIRMED)
    return _m


def set_holding_override(entity_id: str, term: str) -> Callable[[TaxCase], None]:
    def _m(c: TaxCase) -> None:
        for t in c.income.capital_gains:
            if t.id == entity_id:
                t.holding_override = term
    return _m


def set_asset_class(entity_id: str, asset_class: str) -> Callable[[TaxCase], None]:
    def _m(c: TaxCase) -> None:
        for t in c.income.capital_gains:
            if t.id == entity_id:
                t.asset_class = asset_class
    return _m
