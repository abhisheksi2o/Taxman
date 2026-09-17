"""Output schema of the deterministic engine – a fully expandable explanation tree."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.models.common import EvidenceRef, Money, ValueStatus, utcnow

LineKind = Literal["input", "subtotal", "deduction", "tax", "credit", "result", "info", "adjustment", "interest"]


class Line(BaseModel):
    id: str
    label: str
    amount: Money = Decimal("0")
    kind: LineKind = "input"
    formula: str | None = None
    rule_ref: str | None = None
    status: ValueStatus | None = None
    sources: list[EvidenceRef] = Field(default_factory=list)
    children: list["Line"] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    entity_id: str | None = None

    def add(self, child: "Line") -> "Line":
        self.children.append(child)
        return child

    def find(self, line_id: str) -> "Line | None":
        if self.id == line_id:
            return self
        for c in self.children:
            hit = c.find(line_id)
            if hit:
                return hit
        return None


Line.model_rebuild()


class RegimeSummary(BaseModel):
    regime: Literal["OLD", "NEW"]
    gross_salary: Money = Decimal("0")
    income_salary: Money = Decimal("0")
    income_house_property: Money = Decimal("0")
    income_capital_gains: Money = Decimal("0")
    income_business: Money = Decimal("0")
    income_other_sources: Money = Decimal("0")
    exempt_income: Money = Decimal("0")
    gross_total_income: Money = Decimal("0")
    total_deductions: Money = Decimal("0")
    taxable_income: Money = Decimal("0")
    tax_on_normal_income: Money = Decimal("0")
    tax_on_special_income: Money = Decimal("0")
    tax_before_rebate: Money = Decimal("0")
    rebate_87a: Money = Decimal("0")
    tax_after_rebate: Money = Decimal("0")
    surcharge: Money = Decimal("0")
    cess: Money = Decimal("0")
    total_tax_liability: Money = Decimal("0")
    tds: Money = Decimal("0")
    tcs: Money = Decimal("0")
    advance_tax: Money = Decimal("0")
    self_assessment_tax: Money = Decimal("0")
    total_credits: Money = Decimal("0")
    interest_234a: Money = Decimal("0")
    interest_234b: Money = Decimal("0")
    interest_234c: Money = Decimal("0")
    total_interest: Money = Decimal("0")
    net_payable: Money = Decimal("0")  # positive = payable, negative = refund
    refund_due: Money = Decimal("0")
    balance_payable: Money = Decimal("0")
    effective_tax_rate: Money = Decimal("0")


class RegimeComputation(BaseModel):
    regime: Literal["OLD", "NEW"]
    label: str
    summary: RegimeSummary
    lines: list[Line]
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    credit_ledger: list[dict] = Field(default_factory=list)

    def find_line(self, line_id: str) -> Line | None:
        for l in self.lines:
            hit = l.find(line_id)
            if hit:
                return hit
        return None


class ComparisonRow(BaseModel):
    key: str
    label: str
    old: Money
    new: Money
    difference: Money  # new - old


class RegimeComparison(BaseModel):
    rows: list[ComparisonRow]
    lower_tax_regime: Literal["OLD", "NEW", "EQUAL"]
    difference: Money  # |old - new| on estimated payable
    explanation: list[str]
    assumptions: list[str]
    statutory_default: Literal["OLD", "NEW"]


class TaxComputation(BaseModel):
    case_id: str
    assessment_year: str
    financial_year: str
    rules_version: str
    rules_status: str
    computed_at: datetime = Field(default_factory=utcnow)
    as_of: str
    age_category: str
    residential_status: str
    old: RegimeComputation
    new: RegimeComputation
    comparison: RegimeComparison
    statutory_default_regime: Literal["OLD", "NEW"]
    validation_warnings: list[str] = Field(default_factory=list)

    def regime(self, code: str) -> RegimeComputation:
        return self.old if code == "OLD" else self.new
