"""The unified, source-agnostic taxpayer financial model.

This is the single representation that the reconciliation engine, the deterministic tax engine,
the readiness checker and Ask Astra all read from. Document extractors *write into* it; nothing
document-specific is stored here beyond provenance references.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from .common import (
    EvidenceRef,
    Money,
    ResidentialStatus,
    SourceType,
    TracedValue,
    ValueStatus,
    new_id,
    utcnow,
)

IncomeSourceCode = Literal[
    "SALARY", "INTEREST", "DIVIDEND", "CAPITAL_GAINS", "RENTAL", "BUSINESS", "FREELANCE", "FOREIGN", "OTHER"
]

# --------------------------------------------------------------------------- taxpayer profile


class PreviousReturn(BaseModel):
    assessment_year: str
    itr_form: str | None = None
    regime: Literal["OLD", "NEW"] | None = None
    gross_total_income: Money = Decimal("0")
    total_deductions: Money = Decimal("0")
    taxable_income: Money = Decimal("0")
    total_tax: Money = Decimal("0")
    tds: Money = Decimal("0")
    refund_or_payable: Money = Decimal("0")  # positive = payable, negative = refund
    income_heads: dict[str, Money] = Field(default_factory=dict)
    filed_on: date | None = None
    document_id: str | None = None


class BankAccount(BaseModel):
    id: str = Field(default_factory=lambda: new_id("bank"))
    bank_name: str
    account_number_masked: str
    ifsc: str | None = None
    account_type: Literal["SAVINGS", "CURRENT", "NRO", "NRE", "OTHER"] = "SAVINGS"
    is_primary_for_refund: bool = False
    status: ValueStatus = ValueStatus.USER_ENTERED
    document_ids: list[str] = Field(default_factory=list)


class TaxpayerProfile(BaseModel):
    assessment_year: str = "2026-27"
    name: str | None = None
    pan: str | None = None  # stored only inside the encrypted case blob; masked in every API response
    date_of_birth: date | None = None
    taxpayer_type: Literal["INDIVIDUAL", "HUF"] = "INDIVIDUAL"
    residential_status: ResidentialStatus = ResidentialStatus.RESIDENT
    employment_status: Literal["SALARIED", "SELF_EMPLOYED", "BOTH", "RETIRED", "NOT_EMPLOYED"] | None = None
    income_sources: list[IncomeSourceCode] = Field(default_factory=list)
    employer_count: int | None = None
    has_investments: bool | None = None
    has_rental_income: bool | None = None
    property_count: int | None = None
    has_home_loan: bool | None = None
    has_foreign_income_or_assets: bool | None = None
    filed_previous_return: bool | None = None
    city_type: Literal["METRO", "NON_METRO"] | None = None
    regime_preference: Literal["OLD", "NEW", "UNDECIDED"] = "UNDECIDED"
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    onboarding_completed: bool = False
    field_status: dict[str, ValueStatus] = Field(default_factory=dict)

    def status_of(self, field: str) -> ValueStatus:
        return self.field_status.get(field, ValueStatus.USER_ENTERED)


# --------------------------------------------------------------------------- income entities


class SalaryIncome(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sal"))
    employer_name: str
    employer_tan: str | None = None
    period_from: date | None = None
    period_to: date | None = None
    gross_salary: TracedValue  # salary u/s 17(1) + perquisites 17(2) + profits in lieu 17(3)
    basic_salary: TracedValue | None = None
    hra_received: TracedValue | None = None
    exempt_allowances: TracedValue = Field(default_factory=TracedValue.zero)  # u/s 10 as reported
    professional_tax: TracedValue = Field(default_factory=TracedValue.zero)  # 16(iii)
    employer_nps_contribution: TracedValue = Field(default_factory=TracedValue.zero)  # 80CCD(2)
    tds: TracedValue = Field(default_factory=TracedValue.zero)  # as per Form 16 Part A / slips
    rent_paid: TracedValue | None = None  # for HRA computation (old regime)
    status: ValueStatus = ValueStatus.EXTRACTED
    document_ids: list[str] = Field(default_factory=list)


InterestKind = Literal["SAVINGS", "FIXED_DEPOSIT", "RECURRING_DEPOSIT", "BONDS", "INCOME_TAX_REFUND", "OTHER"]


class InterestIncome(BaseModel):
    id: str = Field(default_factory=lambda: new_id("int"))
    payer_name: str
    payer_tan: str | None = None
    account_ref: str | None = None  # masked
    kind: InterestKind = "SAVINGS"
    amount: TracedValue
    tds: TracedValue = Field(default_factory=TracedValue.zero)
    period_from: date | None = None
    period_to: date | None = None
    transaction_refs: list[str] = Field(default_factory=list)
    status: ValueStatus = ValueStatus.EXTRACTED
    document_ids: list[str] = Field(default_factory=list)


class DividendIncome(BaseModel):
    id: str = Field(default_factory=lambda: new_id("div"))
    payer_name: str
    isin: str | None = None
    amount: TracedValue
    tds: TracedValue = Field(default_factory=TracedValue.zero)
    paid_on: date | None = None
    status: ValueStatus = ValueStatus.EXTRACTED
    document_ids: list[str] = Field(default_factory=list)


class RentalIncome(BaseModel):
    id: str = Field(default_factory=lambda: new_id("hp"))
    property_name: str
    address: str | None = None
    is_self_occupied: bool = False
    annual_rent_received: TracedValue = Field(default_factory=TracedValue.zero)
    municipal_taxes_paid: TracedValue = Field(default_factory=TracedValue.zero)
    interest_on_borrowed_capital: TracedValue = Field(default_factory=TracedValue.zero)  # 24(b)
    ownership_share: Money = Decimal("1")
    tenant_name: str | None = None
    tenant_tds: TracedValue = Field(default_factory=TracedValue.zero)  # 194-IB / 194-I
    status: ValueStatus = ValueStatus.USER_ENTERED
    document_ids: list[str] = Field(default_factory=list)


AssetClass = Literal[
    "LISTED_EQUITY", "EQUITY_MF", "DEBT_MF", "LISTED_BOND", "UNLISTED_SHARES", "IMMOVABLE_PROPERTY", "GOLD", "OTHER"
]


class CapitalGainTransaction(BaseModel):
    id: str = Field(default_factory=lambda: new_id("cg"))
    asset_class: AssetClass = "LISTED_EQUITY"
    description: str
    isin: str | None = None
    quantity: Money | None = None
    acquisition_date: date | None = None
    transfer_date: date
    sale_consideration: TracedValue
    cost_of_acquisition: TracedValue
    transfer_expenses: TracedValue = Field(default_factory=TracedValue.zero)
    improvement_cost: TracedValue = Field(default_factory=TracedValue.zero)
    stt_paid: bool = True
    holding_override: Literal["SHORT", "LONG"] | None = None
    broker_ref: str | None = None
    status: ValueStatus = ValueStatus.EXTRACTED
    document_ids: list[str] = Field(default_factory=list)


class BusinessIncome(BaseModel):
    id: str = Field(default_factory=lambda: new_id("bus"))
    description: str
    nature: Literal["PROFESSION_44ADA", "BUSINESS_44AD", "REGULAR"] = "PROFESSION_44ADA"
    gross_receipts: TracedValue
    expenses: TracedValue = Field(default_factory=TracedValue.zero)
    declared_profit: TracedValue | None = None  # if the taxpayer declares more than the presumptive minimum
    digital_receipts_share: Money = Decimal("1")  # affects 44AD rate & 44ADA threshold
    tds: TracedValue = Field(default_factory=TracedValue.zero)
    status: ValueStatus = ValueStatus.USER_ENTERED
    document_ids: list[str] = Field(default_factory=list)


class OtherIncome(BaseModel):
    id: str = Field(default_factory=lambda: new_id("oth"))
    description: str
    category: Literal["FAMILY_PENSION", "COMMISSION", "GIFT", "LOTTERY", "OTHER"] = "OTHER"
    amount: TracedValue
    tds: TracedValue = Field(default_factory=TracedValue.zero)
    status: ValueStatus = ValueStatus.USER_ENTERED
    document_ids: list[str] = Field(default_factory=list)


class ExemptIncome(BaseModel):
    id: str = Field(default_factory=lambda: new_id("exm"))
    description: str
    category: Literal["PPF_INTEREST", "AGRICULTURAL", "GRATUITY", "OTHER"] = "OTHER"
    amount: TracedValue
    status: ValueStatus = ValueStatus.USER_ENTERED
    document_ids: list[str] = Field(default_factory=list)


class IncomeBundle(BaseModel):
    salary: list[SalaryIncome] = Field(default_factory=list)
    interest: list[InterestIncome] = Field(default_factory=list)
    dividend: list[DividendIncome] = Field(default_factory=list)
    rental: list[RentalIncome] = Field(default_factory=list)
    capital_gains: list[CapitalGainTransaction] = Field(default_factory=list)
    business: list[BusinessIncome] = Field(default_factory=list)
    other_sources: list[OtherIncome] = Field(default_factory=list)
    exempt: list[ExemptIncome] = Field(default_factory=list)

    def all_entities(self) -> list[Any]:
        return [
            *self.salary, *self.interest, *self.dividend, *self.rental,
            *self.capital_gains, *self.business, *self.other_sources, *self.exempt,
        ]


# --------------------------------------------------------------------------- taxes paid


class TaxDeducted(BaseModel):
    """One TDS/TCS line as reported by ONE source. Several lines may describe the same deduction
    (Form 16 vs 26AS vs AIS) – the reconciliation engine matches them and the tax engine picks the
    creditable amount transparently."""

    id: str = Field(default_factory=lambda: new_id("tds"))
    kind: Literal["TDS", "TCS"] = "TDS"
    deductor_name: str
    deductor_tan: str | None = None
    section: str = "192"
    amount_paid_credited: TracedValue = Field(default_factory=TracedValue.zero)
    tax_deducted: TracedValue
    period: str | None = None  # e.g. "Q1 FY2025-26"
    source_type: SourceType = SourceType.FORM26AS
    linked_income_id: str | None = None
    status: ValueStatus = ValueStatus.EXTRACTED
    document_ids: list[str] = Field(default_factory=list)


class TaxPayment(BaseModel):
    id: str = Field(default_factory=lambda: new_id("pay"))
    kind: Literal["ADVANCE_TAX", "SELF_ASSESSMENT_TAX"] = "ADVANCE_TAX"
    amount: TracedValue
    paid_on: date
    challan_ref: str | None = None
    status: ValueStatus = ValueStatus.USER_ENTERED
    document_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- deductions / investments / loans

DeductionSection = Literal[
    "80C", "80CCC", "80CCD1", "80CCD1B", "80CCD2", "80D", "80DD", "80DDB", "80E", "80EE", "80EEA",
    "80EEB", "80G", "80GG", "80GGA", "80GGC", "80TTA", "80TTB", "80U",
]


class Deduction(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ded"))
    section: DeductionSection
    description: str
    amount: TracedValue
    qualifying_rate: Money | None = None  # 80G: 0.5 / 1.0
    for_senior_parents: bool = False  # 80D parents bucket
    supporting_document_ids: list[str] = Field(default_factory=list)
    status: ValueStatus = ValueStatus.USER_ENTERED


class Investment(BaseModel):
    id: str = Field(default_factory=lambda: new_id("inv"))
    instrument: Literal[
        "PPF", "ELSS", "LIFE_INSURANCE", "EPF", "NSC", "TAX_SAVER_FD", "NPS", "SSY", "HOME_LOAN_PRINCIPAL",
        "TUITION_FEES", "HEALTH_INSURANCE", "OTHER",
    ]
    provider: str | None = None
    amount: TracedValue
    invested_on: date | None = None
    section_hint: DeductionSection | None = None
    status: ValueStatus = ValueStatus.USER_ENTERED
    document_ids: list[str] = Field(default_factory=list)


class Loan(BaseModel):
    id: str = Field(default_factory=lambda: new_id("loan"))
    kind: Literal["HOME", "EDUCATION", "ELECTRIC_VEHICLE"] = "HOME"
    lender: str
    interest_paid: TracedValue
    principal_repaid: TracedValue = Field(default_factory=TracedValue.zero)
    property_id: str | None = None  # RentalIncome.id
    sanction_date: date | None = None
    status: ValueStatus = ValueStatus.USER_ENTERED
    document_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- documents


class DocumentRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("doc"))
    type: SourceType
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    storage_key: str
    uploaded_at: datetime = Field(default_factory=utcnow)
    status: Literal["UPLOADED", "PROCESSING", "EXTRACTED", "NEEDS_REVIEW", "FAILED"] = "UPLOADED"
    extraction_method: Literal["RULES", "LLM", "OCR", "MANUAL", "NONE"] = "NONE"
    extraction_confidence: float = 0.0
    detected_type_confidence: float = 0.0
    extracted_fields: dict[str, Any] = Field(default_factory=dict)  # normalized, human-readable summary
    observations: list[dict[str, Any]] = Field(default_factory=list)  # normalized facts used by reconciliation
    flags: list[str] = Field(default_factory=list)  # ambiguous extraction notes
    linked_entity_ids: list[str] = Field(default_factory=list)
    period_label: str | None = None
    page_count: int | None = None
    text_excerpt: str | None = None  # short excerpt for "View source" (never the full document)


# --------------------------------------------------------------------------- reconciliation

ReconKind = Literal["MISSING", "MISMATCH", "DUPLICATE", "CLASSIFICATION", "TIMING"]
Severity = Literal["HIGH", "MEDIUM", "LOW", "INFO"]
ReconCategory = Literal[
    "SALARY", "INTEREST", "DIVIDEND", "CAPITAL_GAINS", "RENTAL", "TDS", "BUSINESS", "OTHER", "PROFILE",
    "DEDUCTION", "DOCUMENT", "PREVIOUS_YEAR",
]


class SuggestedResolution(BaseModel):
    action: Literal[
        "ADD_INCOME", "ADD_TDS", "CONFIRM_ENTITY", "REMOVE_DUPLICATE", "RECLASSIFY", "UPLOAD_DOCUMENT",
        "REVIEW", "UPDATE_PROFILE",
    ]
    label: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ReconciliationItem(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rec"))
    fingerprint: str
    kind: ReconKind
    severity: Severity
    category: ReconCategory
    title: str
    description: str  # neutral language – never accusatory
    evidence: list[EvidenceRef] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    impact_estimate: Money | None = None  # change in estimated tax if resolved as suggested
    impact_note: str | None = None
    required_action: str
    suggested_resolutions: list[SuggestedResolution] = Field(default_factory=list)
    status: Literal["OPEN", "RESOLVED", "DISMISSED"] = "OPEN"
    resolution_note: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    detected_by: str = "reconciliation-engine"


# --------------------------------------------------------------------------- review / declarations


class ReviewState(BaseModel):
    declarations: dict[str, bool] = Field(default_factory=dict)
    confirmed: bool = False
    confirmed_at: datetime | None = None
    selected_regime: Literal["OLD", "NEW"] | None = None
    acknowledged_open_issues: bool = False
    return_package_id: str | None = None


# --------------------------------------------------------------------------- root


class CaseMeta(BaseModel):
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    version: int = 1
    demo_scenario: str | None = None
    demo_seed: int | None = None
    last_reconciliation_at: datetime | None = None
    last_computation_at: datetime | None = None


class TaxCase(BaseModel):
    """Root of the unified model – one per taxpayer per assessment year."""

    id: str = Field(default_factory=lambda: new_id("case"))
    owner_user_id: str
    label: str = "My tax return"
    taxpayer: TaxpayerProfile = Field(default_factory=TaxpayerProfile)
    income: IncomeBundle = Field(default_factory=IncomeBundle)
    tax_deducted: list[TaxDeducted] = Field(default_factory=list)
    tax_payments: list[TaxPayment] = Field(default_factory=list)
    deductions: list[Deduction] = Field(default_factory=list)
    investments: list[Investment] = Field(default_factory=list)
    loans: list[Loan] = Field(default_factory=list)
    bank_accounts: list[BankAccount] = Field(default_factory=list)
    documents: list[DocumentRecord] = Field(default_factory=list)
    reconciliation_items: list[ReconciliationItem] = Field(default_factory=list)
    previous_return: PreviousReturn | None = None
    review: ReviewState = Field(default_factory=ReviewState)
    meta: CaseMeta = Field(default_factory=CaseMeta)

    # ---- helpers -------------------------------------------------------------------------
    @property
    def assessment_year(self) -> str:
        return self.taxpayer.assessment_year

    def find_entity(self, entity_id: str) -> Any | None:
        for e in self.income.all_entities():
            if e.id == entity_id:
                return e
        for coll in (self.tax_deducted, self.tax_payments, self.deductions, self.investments, self.loans,
                     self.bank_accounts, self.documents, self.reconciliation_items):
            for e in coll:
                if e.id == entity_id:
                    return e
        return None

    def document(self, document_id: str) -> DocumentRecord | None:
        return next((d for d in self.documents if d.id == document_id), None)

    def open_items(self) -> list[ReconciliationItem]:
        return [i for i in self.reconciliation_items if i.status == "OPEN"]

    def touch(self) -> None:
        self.meta.updated_at = utcnow()
        self.meta.version += 1
