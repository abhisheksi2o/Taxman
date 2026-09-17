"""Document intelligence pipeline.

upload -> sniff -> text -> detect type -> extract -> normalise -> attach to the taxpayer model (with provenance)
-> store the document reference -> assign confidence -> flag ambiguity.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pypdf import PdfReader

from app.models.common import SourceType, TracedValue, ValueStatus, fy_bounds, money, new_id
from app.models.tax_model import (
    CapitalGainTransaction,
    DividendIncome,
    DocumentRecord,
    InterestIncome,
    Investment,
    Loan,
    PreviousReturn,
    SalaryIncome,
    TaxCase,
    TaxDeducted,
    TaxPayment,
)

from .detectors import detect_type
from .extractors.common import ExtractionResult, normalize_key, parse_date
from .extractors.forms import (
    extract_form16,
    extract_form16a,
    extract_form26as,
    extract_home_loan_certificate,
    extract_interest_certificate,
    extract_rent_receipts,
    extract_salary_slip,
)
from .extractors.portal import extract_ais, extract_investment_proof, extract_previous_itr, extract_tis
from .extractors.statements import extract_bank_statement, extract_broker_statement, extract_dividend_statement
from .llm_extractor import extract_with_llm, llm_available

log = logging.getLogger("astra.documents")

ALLOWED_MIME = {
    "application/pdf": "pdf",
    "text/csv": "csv",
    "application/vnd.ms-excel": "csv",
    "application/json": "json",
    "text/plain": "txt",
    "image/png": "image",
    "image/jpeg": "image",
    "image/webp": "image",
}


class DocumentError(ValueError):
    pass


@dataclass
class RawDocument:
    filename: str
    mime_type: str
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def sniff_mime(filename: str, declared: str | None, data: bytes) -> str:
    head = data[:8]
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    text = data[:4000].decode("utf-8", errors="ignore").lstrip()
    if text.startswith("{") or text.startswith("["):
        return "application/json"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "csv" or (declared or "").endswith("csv"):
        return "text/csv"
    if ext == "json":
        return "application/json"
    if ext in ("txt", "text"):
        return "text/plain"
    if "," in text and "\n" in text and len(text.splitlines()[0].split(",")) >= 3:
        return "text/csv"
    if declared in ALLOWED_MIME:
        return declared
    return "text/plain"


def extract_text(raw: RawDocument) -> tuple[str, int | None]:
    if raw.mime_type == "application/pdf":
        reader = PdfReader(io.BytesIO(raw.data))
        pages = [p.extract_text() or "" for p in reader.pages]
        return "\n".join(pages), len(pages)
    if raw.mime_type.startswith("image/"):
        return "", None
    return raw.data.decode("utf-8", errors="replace"), None


EXTRACTORS = {
    SourceType.FORM16: extract_form16,
    SourceType.FORM16A: extract_form16a,
    SourceType.FORM26AS: extract_form26as,
    SourceType.INTEREST_CERTIFICATE: extract_interest_certificate,
    SourceType.SALARY_SLIP: extract_salary_slip,
    SourceType.HOME_LOAN_CERTIFICATE: extract_home_loan_certificate,
    SourceType.RENT_RECEIPT: extract_rent_receipts,
    SourceType.AIS: extract_ais,
    SourceType.TIS: extract_tis,
    SourceType.PREVIOUS_ITR: extract_previous_itr,
    SourceType.INVESTMENT_PROOF: extract_investment_proof,
    SourceType.DIVIDEND_STATEMENT: extract_dividend_statement,
    SourceType.BROKER_STATEMENT: extract_broker_statement,
    SourceType.CAPITAL_GAINS_STATEMENT: extract_broker_statement,
}


def run_extraction(text: str, doc_type: SourceType, assessment_year: str) -> ExtractionResult:
    if doc_type == SourceType.BANK_STATEMENT:
        s, e = fy_bounds(assessment_year)
        return extract_bank_statement(text, s, e)
    fn = EXTRACTORS.get(doc_type)
    if fn is None:
        r = ExtractionResult()
        r.flags.append("No rule-based extractor for this document type – manual entry required.")
        r.score(0, 1)
        return r
    try:
        return fn(text)
    except Exception as exc:  # noqa: BLE001 – a bad document must never crash the request
        log.exception("extraction failed for %s", doc_type)
        r = ExtractionResult()
        r.flags.append(f"Extraction failed: {exc.__class__.__name__}")
        r.score(0, 1)
        return r


def _tv(amount, source: SourceType, doc_id: str, reference: str, confidence: float, status: ValueStatus = ValueStatus.EXTRACTED) -> TracedValue:
    return TracedValue.of(amount or 0, source, document_id=doc_id, reference=reference, confidence=confidence, status=status)


def _d(v) -> date | None:
    return parse_date(v) if isinstance(v, str) else v


# --------------------------------------------------------------------------- attaching to the model


def attach_to_model(case: TaxCase, doc: DocumentRecord, result: ExtractionResult) -> list[str]:
    """Create / update model entities from an extraction result. Returns the linked entity ids.

    Income documents (Form 16, interest certificates, broker/dividend statements, loan certificates) create
    entities with status EXTRACTED. Information statements (AIS/TIS/26AS/bank statements) only feed the TDS
    ledger and the reconciliation engine – income is never added silently from them.
    """
    f = result.fields
    conf = result.confidence
    src = doc.type
    linked: list[str] = []
    status = ValueStatus.AI_SUGGESTED if doc.extraction_method == "LLM" else ValueStatus.EXTRACTED

    if src == SourceType.FORM16 and f.get("gross_salary") is not None:
        key = f.get("employer_tan") or normalize_key(f.get("employer_name"))
        existing = next((s for s in case.income.salary if (s.employer_tan or normalize_key(s.employer_name)) == key), None)
        sal = existing or SalaryIncome(employer_name=f.get("employer_name") or "Employer", employer_tan=f.get("employer_tan"),
                                       gross_salary=_tv(0, src, doc.id, "Part B · 1(d)", conf, status))
        sal.employer_name = f.get("employer_name") or sal.employer_name
        sal.employer_tan = f.get("employer_tan") or sal.employer_tan
        sal.period_from = _d(f.get("period_from")) or sal.period_from
        sal.period_to = _d(f.get("period_to")) or sal.period_to
        sal.gross_salary = _tv(f["gross_salary"], src, doc.id, "Part B · 1(d) Gross salary", conf, status)
        sal.exempt_allowances = _tv(f.get("exempt_allowances_total") or 0, src, doc.id, "Part B · 2 Exempt allowances u/s 10", conf, status)
        sal.professional_tax = _tv(f.get("professional_tax") or 0, src, doc.id, "Part B · 4(c) Tax on employment", conf, status)
        sal.employer_nps_contribution = _tv(f.get("employer_nps_80ccd2") or 0, src, doc.id, "Part B · 80CCD(2)", conf, status)
        sal.tds = _tv(f.get("total_tds") or 0, src, doc.id, "Part A · Total tax deducted", conf, status)
        if f.get("basic_salary"):
            sal.basic_salary = _tv(f["basic_salary"], src, doc.id, "Annexure · Basic", conf, status)
        if f.get("hra_received"):
            sal.hra_received = _tv(f["hra_received"], src, doc.id, "Annexure · HRA", conf, status)
        sal.status = status
        if doc.id not in sal.document_ids:
            sal.document_ids.append(doc.id)
        if existing is None:
            case.income.salary.append(sal)
        linked.append(sal.id)
        if f.get("total_tds") is not None:
            _upsert_tds(case, doc, src, f.get("employer_name") or "Employer", f.get("employer_tan"), "192",
                        f.get("total_amount_paid") or f["gross_salary"], f["total_tds"], "Part A · Total", conf, linked, linked_income_id=sal.id)

    elif src == SourceType.FORM16A and f.get("total_tds") is not None:
        _upsert_tds(case, doc, src, f.get("deductor_name") or "Deductor", f.get("deductor_tan"), (f.get("section") or "194A").replace("-", ""),
                    f.get("total_amount_paid") or 0, f["total_tds"], "Form 16A · Total", conf, linked)

    elif src == SourceType.FORM26AS:
        for d in f.get("deductors", []):
            _upsert_tds(case, doc, src, d["name"], d["tan"], d["section"], d["amount_paid"], d["tax_deducted"], f"Part I · {d['name']} · {d['section']}", conf, linked)
        for p in f.get("tax_payments", []):
            kind = "ADVANCE_TAX" if p["minor_head"] == "100" else "SELF_ASSESSMENT_TAX"
            if not any(tp.challan_ref == p["challan"] for tp in case.tax_payments):
                tp = TaxPayment(kind=kind, amount=_tv(p["total"], src, doc.id, f"Part III · challan {p['challan']}", conf, status), paid_on=_d(p["date"]) or date.today(),
                                challan_ref=p["challan"], status=status, document_ids=[doc.id])
                case.tax_payments.append(tp)
                linked.append(tp.id)

    elif src == SourceType.AIS:
        for o in result.observations:
            if o.get("category") == "TDS" and o.get("tax_deducted"):
                _upsert_tds(case, doc, src, o.get("counterparty") or "Deductor", o.get("counterparty_tan"), o.get("section") or "", o.get("amount") or 0,
                            o["tax_deducted"], o.get("reference") or "AIS Part B", 0.95, linked)

    elif src == SourceType.INTEREST_CERTIFICATE:
        bank = f.get("bank_name") or "Bank"
        for row in f.get("rows", []):
            existing = next((i for i in case.income.interest if normalize_key(i.payer_name) == normalize_key(bank) and i.account_ref == row["account"] and i.kind == row["kind"]), None)
            ii = existing or InterestIncome(payer_name=bank, kind=row["kind"], account_ref=row["account"], amount=_tv(0, src, doc.id, "", conf))
            ii.amount = _tv(row["interest"], src, doc.id, f"Certificate · {row['account']} ({row['type']})", conf, status)
            ii.tds = _tv(row["tds"], src, doc.id, f"Certificate · {row['account']} TDS", conf, status)
            ii.payer_tan = f.get("deductor_tan") or ii.payer_tan
            ii.status = status
            if doc.id not in ii.document_ids:
                ii.document_ids.append(doc.id)
            if existing is None:
                case.income.interest.append(ii)
            linked.append(ii.id)
        if f.get("total_tds") and float(f["total_tds"]) > 0:
            _upsert_tds(case, doc, src, bank, f.get("deductor_tan"), "194A", f.get("total_interest") or 0, f["total_tds"], "Certificate · Total TDS", conf, linked)

    elif src in (SourceType.BROKER_STATEMENT, SourceType.CAPITAL_GAINS_STATEMENT):
        for t in f.get("trades", []):
            if not t.get("sell_date"):
                continue
            if any(c.broker_ref == t["ref"] and c.description.startswith(t["symbol"]) for c in case.income.capital_gains):
                continue
            cg = CapitalGainTransaction(asset_class=t["asset_class"], description=f"{t['symbol']} × {t['quantity']:g}" if t.get("quantity") else t["symbol"], isin=t.get("isin"),
                                        quantity=Decimal(str(t["quantity"])) if t.get("quantity") is not None else None, acquisition_date=_d(t.get("buy_date")),
                                        transfer_date=_d(t["sell_date"]),
                                        sale_consideration=_tv(t["sell_value"], src, doc.id, f"Trade {t['ref']} · sell value", conf, status),
                                        cost_of_acquisition=_tv(t["buy_value"], src, doc.id, f"Trade {t['ref']} · buy value", conf, status),
                                        transfer_expenses=_tv(t.get("charges") or 0, src, doc.id, f"Trade {t['ref']} · charges", conf, status),
                                        broker_ref=t["ref"], status=status, document_ids=[doc.id])
            case.income.capital_gains.append(cg)
            linked.append(cg.id)

    elif src == SourceType.DIVIDEND_STATEMENT:
        for row in f.get("rows", []):
            if any(d.payer_name == row["company"] and d.paid_on == _d(row.get("date")) and float(d.amount.amount) == row["gross"] for d in case.income.dividend):
                continue
            dv = DividendIncome(payer_name=row["company"], isin=row.get("isin"), amount=_tv(row["gross"], src, doc.id, f"Dividend · {row['company']} · {row.get('date')}", conf, status),
                                tds=_tv(row.get("tds") or 0, src, doc.id, f"Dividend TDS · {row['company']}", conf, status), paid_on=_d(row.get("date")), status=status, document_ids=[doc.id])
            case.income.dividend.append(dv)
            linked.append(dv.id)

    elif src == SourceType.HOME_LOAN_CERTIFICATE and f.get("interest_paid") is not None:
        lender = f.get("lender") or "Lender"
        existing = next((l for l in case.loans if l.kind == "HOME" and normalize_key(l.lender) == normalize_key(lender)), None)
        loan = existing or Loan(kind="HOME", lender=lender, interest_paid=_tv(0, src, doc.id, "", conf))
        loan.interest_paid = _tv(f["interest_paid"], src, doc.id, "Certificate · interest paid", conf, status)
        loan.principal_repaid = _tv(f.get("principal_repaid") or 0, src, doc.id, "Certificate · principal repaid", conf, status)
        loan.sanction_date = _d(f.get("sanction_date")) or loan.sanction_date
        loan.status = status
        if doc.id not in loan.document_ids:
            loan.document_ids.append(doc.id)
        if not loan.property_id:
            so = next((p for p in case.income.rental if p.is_self_occupied), None)
            if so:
                loan.property_id = so.id
        if existing is None:
            case.loans.append(loan)
        linked.append(loan.id)

    elif src == SourceType.PREVIOUS_ITR and f.get("assessment_year"):
        case.previous_return = PreviousReturn(assessment_year=f["assessment_year"], itr_form=f.get("itr_form"), regime=f.get("regime"),
                                              gross_total_income=money(f.get("gross_total_income") or 0), total_deductions=money(f.get("total_deductions") or 0),
                                              taxable_income=money(f.get("taxable_income") or 0), total_tax=money(f.get("total_tax") or 0), tds=money(f.get("tds") or 0),
                                              refund_or_payable=money(f.get("refund_or_payable") or 0),
                                              income_heads={k: money(v) for k, v in (f.get("income_heads") or {}).items()},
                                              filed_on=_d(f.get("filed_on")), document_id=doc.id)
        case.taxpayer.filed_previous_return = True

    elif src == SourceType.RENT_RECEIPT and f.get("total_rent_paid"):
        if case.income.salary:
            s = case.income.salary[0]
            s.rent_paid = _tv(f["total_rent_paid"], src, doc.id, f"{len(f.get('receipts', []))} rent receipts", conf, status)
            linked.append(s.id)

    elif src == SourceType.INVESTMENT_PROOF:
        for i in f.get("items", []):
            instrument = str(i.get("instrument", "OTHER")).upper().replace(" ", "_")
            if instrument not in ("PPF", "ELSS", "LIFE_INSURANCE", "EPF", "NSC", "TAX_SAVER_FD", "NPS", "SSY", "HOME_LOAN_PRINCIPAL", "TUITION_FEES", "HEALTH_INSURANCE"):
                instrument = "OTHER"
            inv = Investment(instrument=instrument, provider=i.get("provider"), amount=_tv(i.get("amount") or 0, src, doc.id, f"Proof · {i.get('instrument')}", conf, status),
                             invested_on=_d(i.get("date")), section_hint=i.get("section"), status=status, document_ids=[doc.id])
            case.investments.append(inv)
            linked.append(inv.id)

    elif src == SourceType.SALARY_SLIP and f.get("gross_earnings") is not None:
        # payslips only enrich an existing employer entry (basic / HRA) – they never replace Form 16
        key = normalize_key(f.get("employer_name"))
        sal = next((s for s in case.income.salary if normalize_key(s.employer_name) == key), None)
        if sal is not None:
            if doc.id not in sal.document_ids:
                sal.document_ids.append(doc.id)
            linked.append(sal.id)
    return linked


def _upsert_tds(case: TaxCase, doc: DocumentRecord, src: SourceType, name: str, tan: str | None, section: str, amount_paid, tax_deducted, reference: str,
                conf: float, linked: list[str], linked_income_id: str | None = None) -> None:
    key = tan or normalize_key(name)
    existing = next((t for t in case.tax_deducted if t.source_type == src and (t.deductor_tan or normalize_key(t.deductor_name)) == key and t.section == section), None)
    status = ValueStatus.AI_SUGGESTED if doc.extraction_method == "LLM" else ValueStatus.EXTRACTED
    t = existing or TaxDeducted(deductor_name=name, deductor_tan=tan, section=section, source_type=src, tax_deducted=_tv(0, src, doc.id, reference, conf))
    t.deductor_name = name or t.deductor_name
    t.deductor_tan = tan or t.deductor_tan
    t.amount_paid_credited = _tv(amount_paid, src, doc.id, reference, conf, status)
    t.tax_deducted = _tv(tax_deducted, src, doc.id, reference, conf, status)
    t.linked_income_id = linked_income_id or t.linked_income_id
    t.status = status
    if doc.id not in t.document_ids:
        t.document_ids.append(doc.id)
    if existing is None:
        case.tax_deducted.append(t)
    linked.append(t.id)


# --------------------------------------------------------------------------- orchestration


def ingest(case: TaxCase, raw: RawDocument, hint: SourceType | None = None, storage_key: str | None = None,
           allow_llm: bool = True) -> DocumentRecord:
    mime = sniff_mime(raw.filename, raw.mime_type, raw.data)
    if mime not in ALLOWED_MIME:
        raise DocumentError(f"Unsupported file type: {mime}")
    raw.mime_type = mime
    sha = raw.sha256
    if any(d.sha256 == sha for d in case.documents):
        raise DocumentError("This document has already been uploaded to this return.")
    safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", raw.filename)[:120] or "document"
    doc = DocumentRecord(type=hint or SourceType.OTHER, filename=safe_name, mime_type=mime, size_bytes=len(raw.data), sha256=sha,
                         storage_key=storage_key or f"{case.id}/{new_id('blob')}", status="PROCESSING")
    text, page_count = extract_text(raw)
    doc.page_count = page_count
    doc_type, det_conf = detect_type(text, safe_name, hint)
    doc.type = doc_type
    doc.detected_type_confidence = det_conf
    result: ExtractionResult
    if text.strip():
        doc.extraction_method = "RULES"
        result = run_extraction(text, doc_type, case.assessment_year)
        doc.text_excerpt = "\n".join(text.splitlines()[:25])[:2500]
    else:
        result = ExtractionResult()
        if allow_llm and llm_available():
            fields = extract_with_llm(raw.data, mime, doc_type)
            if fields:
                doc.extraction_method = "LLM"
                result.fields = fields
                result.flags.extend(fields.get("ambiguities", []) or [])
                result.confidence = float(fields.get("confidence", 0.5))
            else:
                doc.extraction_method = "NONE"
                result.flags.append("AI extraction unavailable or failed – please enter the values manually.")
        else:
            doc.extraction_method = "NONE"
            result.flags.append("Scanned document: no text layer found and AI extraction is not enabled – manual entry required.")
    if doc_type == SourceType.OTHER:
        result.flags.append("Document type could not be determined – choose the type manually to extract data.")
    doc.extracted_fields = _json_safe(result.fields)
    doc.observations = [{**o, "document_id": doc.id, "source_type": doc_type.value, "id": new_id("obs")} for o in result.observations]
    doc.flags = list(dict.fromkeys(result.flags))
    doc.extraction_confidence = result.confidence
    doc.period_label = result.period_label
    linked = attach_to_model(case, doc, result) if result.confidence > 0 else []
    doc.linked_entity_ids = linked
    if result.confidence >= 0.75 and not doc.flags:
        doc.status = "EXTRACTED"
    elif result.confidence > 0:
        doc.status = "NEEDS_REVIEW"
    else:
        doc.status = "FAILED" if not text.strip() and doc.extraction_method == "NONE" else "NEEDS_REVIEW"
    case.documents.append(doc)
    return doc


def _json_safe(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dict):
        return {k: _json_safe(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_json_safe(x) for x in v]
    if isinstance(v, date):
        return v.isoformat()
    return v
