"""Shared case operations used by several routers: recompute, reconcile, persist, audit, entity building."""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from app.core import audit
from app.models.common import SourceType, TracedValue, ValueStatus, money, utcnow
from app.models.tax_model import (
    BankAccount,
    BusinessIncome,
    CapitalGainTransaction,
    Deduction,
    DividendIncome,
    ExemptIncome,
    InterestIncome,
    Investment,
    Loan,
    OtherIncome,
    RentalIncome,
    SalaryIncome,
    TaxCase,
    TaxDeducted,
    TaxPayment,
)
from app.reconciliation.engine import reconcile
from app.tax_engine.engine import compute

if TYPE_CHECKING:  # the repository is any object with save()/audit(); SQLAlchemy is not imported here
    from app.db.repository import CaseRepository


def tv(value: Any, reference: str = "Entered by taxpayer", status: ValueStatus = ValueStatus.USER_ENTERED) -> TracedValue:
    return TracedValue.of(value or 0, SourceType.USER_INPUT, status=status, reference=reference)


def _date(v) -> date | None:
    if not v:
        return None
    return date.fromisoformat(str(v)[:10])


HEADS = {"SALARY", "INTEREST", "DIVIDEND", "RENTAL", "CAPITAL_GAINS", "BUSINESS", "OTHER", "EXEMPT", "TDS", "TAX_PAYMENT", "DEDUCTION", "INVESTMENT", "LOAN", "BANK_ACCOUNT"}


def build_entity(head: str, p: dict, status: ValueStatus = ValueStatus.USER_ENTERED):
    """Construct a model entity from an API payload. All amounts become user-entered traced values."""
    ref = p.get("reference") or "Entered by taxpayer"
    if head == "SALARY":
        return SalaryIncome(employer_name=p["employer_name"], employer_tan=p.get("employer_tan"), period_from=_date(p.get("period_from")), period_to=_date(p.get("period_to")),
                            gross_salary=tv(p.get("gross_salary"), ref, status), basic_salary=(tv(p["basic_salary"], ref, status) if p.get("basic_salary") else None),
                            hra_received=(tv(p["hra_received"], ref, status) if p.get("hra_received") else None), exempt_allowances=tv(p.get("exempt_allowances"), ref, status),
                            professional_tax=tv(p.get("professional_tax"), ref, status), employer_nps_contribution=tv(p.get("employer_nps_contribution"), ref, status),
                            tds=tv(p.get("tds"), ref, status), rent_paid=(tv(p["rent_paid"], ref, status) if p.get("rent_paid") else None), status=status)
    if head == "INTEREST":
        return InterestIncome(payer_name=p["payer_name"], payer_tan=p.get("payer_tan"), account_ref=p.get("account_ref"), kind=p.get("kind", "SAVINGS"), amount=tv(p.get("amount"), ref, status),
                              tds=tv(p.get("tds"), ref, status), status=status)
    if head == "DIVIDEND":
        return DividendIncome(payer_name=p["payer_name"], isin=p.get("isin"), amount=tv(p.get("amount"), ref, status), tds=tv(p.get("tds"), ref, status), paid_on=_date(p.get("paid_on")), status=status)
    if head == "RENTAL":
        return RentalIncome(property_name=p.get("property_name") or "Let-out property", address=p.get("address"), is_self_occupied=bool(p.get("is_self_occupied", False)),
                            annual_rent_received=tv(p.get("annual_rent"), ref, status), municipal_taxes_paid=tv(p.get("municipal_taxes"), ref, status),
                            interest_on_borrowed_capital=tv(p.get("interest_on_loan"), ref, status), ownership_share=money(p.get("ownership_share") or 1), tenant_name=p.get("tenant_name"),
                            tenant_tds=tv(p.get("tenant_tds"), ref, status), status=status)
    if head == "CAPITAL_GAINS":
        return CapitalGainTransaction(asset_class=p.get("asset_class", "LISTED_EQUITY"), description=p["description"], isin=p.get("isin"), quantity=(money(p["quantity"]) if p.get("quantity") else None),
                                      acquisition_date=_date(p.get("acquisition_date")), transfer_date=_date(p["transfer_date"]), sale_consideration=tv(p.get("sale_consideration"), ref, status),
                                      cost_of_acquisition=tv(p.get("cost_of_acquisition"), ref, status), transfer_expenses=tv(p.get("transfer_expenses"), ref, status),
                                      improvement_cost=tv(p.get("improvement_cost"), ref, status), stt_paid=bool(p.get("stt_paid", True)), holding_override=p.get("holding_override"), status=status)
    if head == "BUSINESS":
        return BusinessIncome(description=p.get("description") or "Business / profession", nature=p.get("nature", "PROFESSION_44ADA"), gross_receipts=tv(p.get("amount") or p.get("gross_receipts"), ref, status),
                              expenses=tv(p.get("expenses"), ref, status), declared_profit=(tv(p["declared_profit"], ref, status) if p.get("declared_profit") else None),
                              digital_receipts_share=money(p.get("digital_receipts_share") or 1), tds=tv(p.get("tds"), ref, status), status=status)
    if head == "OTHER":
        return OtherIncome(description=p.get("description") or "Other income", category=p.get("category", "OTHER"), amount=tv(p.get("amount"), ref, status), tds=tv(p.get("tds"), ref, status), status=status)
    if head == "EXEMPT":
        return ExemptIncome(description=p.get("description") or "Exempt income", category=p.get("category", "OTHER"), amount=tv(p.get("amount"), ref, status), status=status)
    if head == "TDS":
        return TaxDeducted(kind=p.get("kind", "TDS"), deductor_name=p["deductor_name"], deductor_tan=p.get("deductor_tan"), section=str(p.get("section", "194A")), amount_paid_credited=tv(p.get("amount_paid"), ref, status),
                           tax_deducted=tv(p.get("tax_deducted"), ref, status), period=p.get("period"), source_type=SourceType.USER_INPUT, status=status)
    if head == "TAX_PAYMENT":
        return TaxPayment(kind=p.get("kind", "ADVANCE_TAX"), amount=tv(p.get("amount"), ref, status), paid_on=_date(p.get("paid_on")) or date.today(), challan_ref=p.get("challan_ref"), status=status)
    if head == "DEDUCTION":
        return Deduction(section=p["section"], description=p.get("description") or p["section"], amount=tv(p.get("amount"), ref, status), qualifying_rate=(money(p["qualifying_rate"]) if p.get("qualifying_rate") else None),
                         for_senior_parents=bool(p.get("for_senior_parents", False)), status=status)
    if head == "INVESTMENT":
        return Investment(instrument=p.get("instrument", "OTHER"), provider=p.get("provider"), amount=tv(p.get("amount"), ref, status), invested_on=_date(p.get("invested_on")), section_hint=p.get("section_hint"), status=status)
    if head == "LOAN":
        return Loan(kind=p.get("kind", "HOME"), lender=p["lender"], interest_paid=tv(p.get("interest_paid"), ref, status), principal_repaid=tv(p.get("principal_repaid"), ref, status), property_id=p.get("property_id"),
                    sanction_date=_date(p.get("sanction_date")), status=status)
    if head == "BANK_ACCOUNT":
        from app.models.common import mask_account

        return BankAccount(bank_name=p["bank_name"], account_number_masked=mask_account(p.get("account_number")) or p.get("account_number_masked", "••••"), ifsc=p.get("ifsc"),
                           account_type=p.get("account_type", "SAVINGS"), is_primary_for_refund=bool(p.get("is_primary_for_refund", False)), status=status)
    raise ValueError(f"Unknown head {head}")


def collection_for(case: TaxCase, head: str) -> list:
    return {
        "SALARY": case.income.salary, "INTEREST": case.income.interest, "DIVIDEND": case.income.dividend, "RENTAL": case.income.rental, "CAPITAL_GAINS": case.income.capital_gains,
        "BUSINESS": case.income.business, "OTHER": case.income.other_sources, "EXEMPT": case.income.exempt, "TDS": case.tax_deducted, "TAX_PAYMENT": case.tax_payments,
        "DEDUCTION": case.deductions, "INVESTMENT": case.investments, "LOAN": case.loans, "BANK_ACCOUNT": case.bank_accounts,
    }[head]


def head_of(case: TaxCase, entity_id: str) -> str | None:
    for head in HEADS:
        if any(e.id == entity_id for e in collection_for(case, head)):
            return head
    return None


def remove_entity(case: TaxCase, entity_id: str) -> bool:
    head = head_of(case, entity_id)
    if head is None:
        return False
    coll = collection_for(case, head)
    coll[:] = [e for e in coll if e.id != entity_id]
    return True


def confirm_entity(case: TaxCase, entity_id: str) -> bool:
    e = case.find_entity(entity_id)
    if e is None or not hasattr(e, "status"):
        return False
    e.status = ValueStatus.USER_CONFIRMED
    for name, value in e.__dict__.items():
        if isinstance(value, TracedValue):
            setattr(e, name, value.confirmed())
    return True


def update_entity(case: TaxCase, entity_id: str, changes: dict) -> bool:
    e = case.find_entity(entity_id)
    if e is None:
        return False
    for k, v in changes.items():
        if not hasattr(e, k) or k in ("id",):
            continue
        current = getattr(e, k)
        if isinstance(current, TracedValue):
            setattr(e, k, tv(v, "Edited by taxpayer", ValueStatus.USER_CONFIRMED))
        elif k.endswith("_date") or k in ("paid_on", "period_from", "period_to", "invested_on", "sanction_date"):
            setattr(e, k, _date(v))
        else:
            setattr(e, k, v)
    if hasattr(e, "status"):
        e.status = ValueStatus.USER_CONFIRMED
    return True


def refresh(case: TaxCase) -> dict:
    """Re-run reconciliation and the deterministic computation; returns a small status dict."""
    items = reconcile(case)
    computation = compute(case)
    case.meta.last_computation_at = utcnow()
    return {"open_items": len([i for i in items if i.status == "OPEN"]), "computation": computation}


def commit(repo: "CaseRepository", case: TaxCase, user_id: str, actor: str, action: str, summary: str, details: dict | None = None,
           evidence: list[dict] | None = None, refresh_case: bool = True) -> dict:
    status = refresh(case) if refresh_case else {}
    repo.save(case)
    audit.record(repo, case.id, user_id, actor, action, summary, details, evidence)
    if refresh_case:
        audit.record(repo, case.id, None, audit.ACTOR_SYSTEM, "reconciliation.run", f"Reconciliation updated – {status['open_items']} open item(s)", {"open_items": status["open_items"]})
        audit.record(repo, case.id, None, audit.ACTOR_SYSTEM, "computation.updated", "Tax calculation updated", {"rules_version": status["computation"].rules_version})
    return status
