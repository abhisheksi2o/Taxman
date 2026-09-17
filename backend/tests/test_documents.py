from app.documents.pipeline import RawDocument, ingest
from app.models.common import SourceType
from app.models.tax_model import TaxCase, TaxpayerProfile
from app.synthetic.generator import generate


def test_form16_roundtrip_through_pdf():
    gen = generate("salaried_basic", 11)
    f16 = next(d for d in gen.documents if d.doc_type == SourceType.FORM16)
    case = TaxCase(owner_user_id="u", taxpayer=TaxpayerProfile(assessment_year="2026-27"))
    doc = ingest(case, RawDocument(f16.filename, "application/pdf", f16.data), allow_llm=False)
    assert doc.type == SourceType.FORM16
    assert doc.extraction_confidence >= 0.9
    assert len(case.income.salary) == 1
    emp = gen.world.employers[0]
    assert float(case.income.salary[0].gross_salary.amount) == float(emp.gross_annual)
    assert float(case.income.salary[0].tds.amount) == float(emp.tds_total)
    assert case.income.salary[0].gross_salary.provenance[0].document_id == doc.id


def test_duplicate_upload_rejected():
    import pytest

    from app.documents.pipeline import DocumentError
    gen = generate("salaried_basic", 12)
    f16 = next(d for d in gen.documents if d.doc_type == SourceType.FORM16)
    case = TaxCase(owner_user_id="u", taxpayer=TaxpayerProfile(assessment_year="2026-27"))
    ingest(case, RawDocument(f16.filename, "application/pdf", f16.data), allow_llm=False)
    with pytest.raises(DocumentError):
        ingest(case, RawDocument(f16.filename, "application/pdf", f16.data), allow_llm=False)


def test_bank_statement_observations_and_no_silent_income():
    gen = generate("missing_income", 5)
    bank = next(d for d in gen.documents if d.doc_type == SourceType.BANK_STATEMENT)
    case = TaxCase(owner_user_id="u", taxpayer=TaxpayerProfile(assessment_year="2026-27"))
    doc = ingest(case, RawDocument(bank.filename, "text/csv", bank.data), allow_llm=False)
    assert doc.type == SourceType.BANK_STATEMENT
    assert any(o["category"] == "BANK_CREDIT" and o["kind"] == "INTEREST" for o in doc.observations)
    assert case.income.interest == []  # information statements never add income silently


def test_unknown_scan_without_llm_is_flagged():
    case = TaxCase(owner_user_id="u", taxpayer=TaxpayerProfile(assessment_year="2026-27"))
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    doc = ingest(case, RawDocument("scan.png", "image/png", png), allow_llm=False)
    assert doc.status == "FAILED"
    assert any("manual entry" in f.lower() for f in doc.flags)
