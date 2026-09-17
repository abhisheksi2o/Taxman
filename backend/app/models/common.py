"""Shared primitives for the unified tax data model.

Every financial number in ASTRA is a ``TracedValue``: an amount plus a status and a list of
provenance records pointing back to the document / user input / calculation that produced it.
Document-specific formats never leak into this model – extractors translate into these types.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

# --------------------------------------------------------------------------- money


def _to_decimal(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, float):
        return Decimal(str(round(v, 2)))
    return Decimal(str(v))


Money = Annotated[
    Decimal,
    BeforeValidator(_to_decimal),
    PlainSerializer(lambda v: float(v) if v is not None else None, return_type=float | None, when_used="json"),
]


def D(v: Any) -> Decimal:
    """Coerce anything numeric to Decimal."""
    if isinstance(v, Decimal):
        return v
    if v is None:
        return Decimal("0")
    return Decimal(str(v))


def money(v: Any) -> Decimal:
    """Round to paise."""
    return D(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def rupees(v: Any) -> Decimal:
    """Round to the nearest rupee (used inside the tax computation)."""
    return D(v).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def round_to_ten(v: Any) -> Decimal:
    """Rounding under sections 288A / 288B – nearest multiple of ten."""
    d = D(v)
    return (d / Decimal(10)).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * Decimal(10)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- enums


class SourceType(str, Enum):
    FORM16 = "FORM16"
    FORM16A = "FORM16A"
    AIS = "AIS"
    TIS = "TIS"
    FORM26AS = "FORM26AS"
    SALARY_SLIP = "SALARY_SLIP"
    BANK_STATEMENT = "BANK_STATEMENT"
    INTEREST_CERTIFICATE = "INTEREST_CERTIFICATE"
    CAPITAL_GAINS_STATEMENT = "CAPITAL_GAINS_STATEMENT"
    BROKER_STATEMENT = "BROKER_STATEMENT"
    DIVIDEND_STATEMENT = "DIVIDEND_STATEMENT"
    RENT_RECEIPT = "RENT_RECEIPT"
    HOME_LOAN_CERTIFICATE = "HOME_LOAN_CERTIFICATE"
    PREVIOUS_ITR = "PREVIOUS_ITR"
    INVESTMENT_PROOF = "INVESTMENT_PROOF"
    USER_INPUT = "USER_INPUT"
    CALCULATION = "CALCULATION"
    AI_EXTRACTION = "AI_EXTRACTION"
    OTHER = "OTHER"


SOURCE_LABELS: dict[SourceType, str] = {
    SourceType.FORM16: "Form 16",
    SourceType.FORM16A: "Form 16A",
    SourceType.AIS: "AIS",
    SourceType.TIS: "TIS",
    SourceType.FORM26AS: "Form 26AS",
    SourceType.SALARY_SLIP: "Salary slip",
    SourceType.BANK_STATEMENT: "Bank statement",
    SourceType.INTEREST_CERTIFICATE: "Interest certificate",
    SourceType.CAPITAL_GAINS_STATEMENT: "Capital gains statement",
    SourceType.BROKER_STATEMENT: "Broker statement",
    SourceType.DIVIDEND_STATEMENT: "Dividend statement",
    SourceType.RENT_RECEIPT: "Rent receipt",
    SourceType.HOME_LOAN_CERTIFICATE: "Home loan certificate",
    SourceType.PREVIOUS_ITR: "Previous ITR",
    SourceType.INVESTMENT_PROOF: "Investment proof",
    SourceType.USER_INPUT: "User input",
    SourceType.CALCULATION: "Calculation",
    SourceType.AI_EXTRACTION: "AI extraction",
    SourceType.OTHER: "Other document",
}


class ValueStatus(str, Enum):
    EXTRACTED = "EXTRACTED"  # read from a document by the extraction pipeline
    CALCULATED = "CALCULATED"  # produced by the deterministic tax engine
    USER_ENTERED = "USER_ENTERED"  # typed in by the taxpayer
    USER_CONFIRMED = "USER_CONFIRMED"  # extracted / suggested and then confirmed by the taxpayer
    AI_SUGGESTED = "AI_SUGGESTED"  # proposed by the AI layer, not yet confirmed
    UNRESOLVED = "UNRESOLVED"  # conflicting sources, needs review


class Regime(str, Enum):
    OLD = "OLD"
    NEW = "NEW"


class AgeCategory(str, Enum):
    GENERAL = "GENERAL"
    SENIOR = "SENIOR"  # 60 to <80 during the financial year
    SUPER_SENIOR = "SUPER_SENIOR"  # 80+


class ResidentialStatus(str, Enum):
    RESIDENT = "RESIDENT"
    RNOR = "RNOR"  # resident but not ordinarily resident
    NON_RESIDENT = "NON_RESIDENT"


# --------------------------------------------------------------------------- provenance


class Provenance(BaseModel):
    """Where a value came from. Every TracedValue carries at least one of these."""

    model_config = ConfigDict(use_enum_values=False)

    source_type: SourceType
    document_id: str | None = None
    reference: str | None = None  # e.g. "Part B · Sl. 1", "Transaction #1847", "AIS item A92"
    confidence: float = 1.0
    page: int | None = None
    note: str | None = None
    recorded_at: datetime = Field(default_factory=utcnow)


class TracedValue(BaseModel):
    amount: Money = Decimal("0")
    status: ValueStatus = ValueStatus.EXTRACTED
    provenance: list[Provenance] = Field(default_factory=list)

    @classmethod
    def of(
        cls,
        amount: Any,
        source: SourceType = SourceType.USER_INPUT,
        *,
        status: ValueStatus | None = None,
        document_id: str | None = None,
        reference: str | None = None,
        confidence: float = 1.0,
        page: int | None = None,
        note: str | None = None,
    ) -> "TracedValue":
        if status is None:
            status = {
                SourceType.USER_INPUT: ValueStatus.USER_ENTERED,
                SourceType.CALCULATION: ValueStatus.CALCULATED,
                SourceType.AI_EXTRACTION: ValueStatus.AI_SUGGESTED,
            }.get(source, ValueStatus.EXTRACTED)
        return cls(
            amount=money(amount),
            status=status,
            provenance=[
                Provenance(
                    source_type=source,
                    document_id=document_id,
                    reference=reference,
                    confidence=confidence,
                    page=page,
                    note=note,
                )
            ],
        )

    @classmethod
    def zero(cls) -> "TracedValue":
        return cls(amount=Decimal("0"), status=ValueStatus.USER_ENTERED, provenance=[])

    @property
    def confidence(self) -> float:
        if not self.provenance:
            return 1.0
        return min(p.confidence for p in self.provenance)

    def confirmed(self) -> "TracedValue":
        return self.model_copy(update={"status": ValueStatus.USER_CONFIRMED})


class EvidenceRef(BaseModel):
    """A pointer used by reconciliation items, alerts and explanations."""

    label: str
    source_type: SourceType
    document_id: str | None = None
    entity_id: str | None = None
    reference: str | None = None
    amount: Money | None = None
    period: str | None = None


def fy_bounds(assessment_year: str) -> tuple[date, date]:
    """'2026-27' -> (2025-04-01, 2026-03-31)."""
    start_year = int(assessment_year.split("-")[0]) - 1
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


def financial_year_label(assessment_year: str) -> str:
    start_year = int(assessment_year.split("-")[0]) - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def mask_pan(pan: str | None) -> str | None:
    if not pan:
        return None
    pan = pan.strip().upper()
    if len(pan) != 10:
        return "•" * len(pan)
    return f"{pan[:3]}••••{pan[-1]}"


def mask_account(number: str | None) -> str | None:
    if not number:
        return None
    digits = "".join(ch for ch in number if ch.isalnum())
    if len(digits) <= 4:
        return "••••"
    return "•" * (len(digits) - 4) + digits[-4:]
