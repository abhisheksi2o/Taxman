"""Extractors for text-based PDF forms: Form 16, Form 16A, Form 26AS, interest certificates, salary slips,
home-loan certificates and rent receipts."""
from __future__ import annotations

import re
from decimal import Decimal

from .common import (
    AMOUNT_RE,
    ExtractionResult,
    after_anchor,
    find_amount,
    find_text,
    mask_account,
    normalize_key,
    obs,
    parse_amount,
    parse_date,
)

TAN_RE = r"[A-Z]{4}\d{5}[A-Z]"
PAN_RE = r"[A-Z]{5}\d{4}[A-Z]"


# --------------------------------------------------------------------------- Form 16


def extract_form16(text: str) -> ExtractionResult:
    r = ExtractionResult()
    f = r.fields
    f["employer_name"] = find_text(text, r"Name and address of the Employer:\s*([^,\n]+)")
    f["employer_tan"] = find_text(text, r"TAN of the Deductor:\s*(" + TAN_RE + ")")
    f["employee_pan"] = find_text(text, r"PAN of the Employee:\s*(" + PAN_RE + ")")
    f["assessment_year"] = find_text(text, r"Assessment Year:\s*(\d{4}-\d{2})")
    m = re.search(r"From\s+(\d{2}-[A-Za-z]{3}-\d{4})\s+To\s+(\d{2}-[A-Za-z]{3}-\d{4})", text)
    f["period_from"] = parse_date(m.group(1)).isoformat() if m else None
    f["period_to"] = parse_date(m.group(2)).isoformat() if m else None
    part_b = after_anchor(text, "PART B")
    gross_block = after_anchor(part_b, "1. Gross Salary")
    f["salary_17_1"] = find_amount(gross_block, r"section 17\(1\)")
    f["perquisites_17_2"] = find_amount(gross_block, r"section 17\(2\)")
    f["profits_17_3"] = find_amount(gross_block, r"section 17\(3\)")
    f["gross_salary"] = find_amount(gross_block, r"\(d\)\s*Total")
    f["hra_exempt"] = find_amount(part_b, r"House rent allowance under section 10\(13A\)")
    f["exempt_allowances_total"] = find_amount(part_b, r"Total amount of exemption claimed under section 10")
    f["standard_deduction"] = find_amount(part_b, r"Standard deduction under section 16\(ia\)")
    f["professional_tax"] = find_amount(part_b, r"Tax on employment under section 16\(iii\)")
    f["income_chargeable_salaries"] = find_amount(part_b, r'Income chargeable under the head "Salaries"[^:]*')
    f["employer_nps_80ccd2"] = find_amount(part_b, r"under section 80CCD\(2\)")
    f["basic_salary"] = find_amount(part_b, r"Basic (?:Pay|Salary)[^:\n]*")
    f["hra_received"] = find_amount(part_b, r"House Rent Allowance \(received\)[^:\n]*")
    opt = find_text(part_b, r"opting out of taxation u/s 115BAC\(1A\)\?:\s*(Yes|No)")
    f["regime_with_employer"] = "OLD" if (opt or "").lower() == "yes" else "NEW"
    rows = []
    for qm in re.finditer(r"^(Q[1-4])\s*\|\s*([A-Z0-9]*)\s*\|\s*" + AMOUNT_RE + r"\s*\|\s*" + AMOUNT_RE + r"\s*\|\s*" + AMOUNT_RE, text, re.M):
        rows.append({"quarter": qm.group(1), "receipt": qm.group(2), "amount_paid": float(parse_amount(qm.group(3)) or 0),
                     "tax_deducted": float(parse_amount(qm.group(4)) or 0), "tax_deposited": float(parse_amount(qm.group(5)) or 0)})
    f["quarterly_tds"] = rows
    tm = re.search(r"^Total\s*\|\s*\|\s*" + AMOUNT_RE + r"\s*\|\s*" + AMOUNT_RE, text, re.M)
    f["total_amount_paid"] = parse_amount(tm.group(1)) if tm else None
    f["total_tds"] = parse_amount(tm.group(2)) if tm else None
    if f["total_tds"] is None and rows:
        f["total_tds"] = Decimal(str(sum(r["tax_deducted"] for r in rows)))
    if rows and f["total_tds"] is not None and abs(sum(r["tax_deducted"] for r in rows) - float(f["total_tds"])) > 1:
        r.flags.append("Quarterly TDS rows do not add up to the certificate total – please verify.")
    if f["gross_salary"] is not None and f["total_amount_paid"] is not None and abs(float(f["gross_salary"]) - float(f["total_amount_paid"])) > 1:
        r.flags.append("Part A amount paid differs from Part B gross salary (common when Part A includes exempt items); review.")
    required = ["employer_name", "employer_tan", "gross_salary", "total_tds", "assessment_year"]
    found = sum(1 for k in required if f.get(k) not in (None, ""))
    r.score(found, len(required), penalty=0.05 * len(r.flags))
    r.period_label = f"FY {f['assessment_year']}" if f.get("assessment_year") else None
    key = f["employer_tan"] or normalize_key(f["employer_name"])
    if f.get("gross_salary") is not None:
        r.observations.append(obs("SALARY", counterparty=f["employer_name"], counterparty_tan=f["employer_tan"], counterparty_key=key,
                                  amount=f["gross_salary"], reference="Part B · 1(d) Gross salary", confidence=r.confidence))
    if f.get("total_tds") is not None:
        r.observations.append(obs("TDS", counterparty=f["employer_name"], counterparty_tan=f["employer_tan"], counterparty_key=key, section="192",
                                  amount=f["total_amount_paid"] or f["gross_salary"], tax_deducted=f["total_tds"], reference="Part A · Total", confidence=r.confidence))
    for row in rows:
        r.observations.append(obs("TDS_QUARTER", counterparty=f["employer_name"], counterparty_tan=f["employer_tan"], counterparty_key=key, section="192",
                                  amount=row["amount_paid"], tax_deducted=row["tax_deducted"], period=row["quarter"], reference=f"Part A · {row['quarter']}"))
    return r


# --------------------------------------------------------------------------- Form 16A


def extract_form16a(text: str) -> ExtractionResult:
    r = ExtractionResult()
    f = r.fields
    f["deductor_name"] = find_text(text, r"Name and address of the Deductor:\s*([^,\n]+)")
    f["deductor_tan"] = find_text(text, r"TAN of the Deductor:\s*(" + TAN_RE + ")")
    f["deductee_pan"] = find_text(text, r"PAN of the Deductee:\s*(" + PAN_RE + ")")
    f["assessment_year"] = find_text(text, r"Assessment Year:\s*(\d{4}-\d{2})")
    f["section"] = find_text(text, r"\(Section\s*([0-9A-Z\-]+)\)") or find_text(text, r"Nature of Payment:.*?(\d{3}[A-Z]{0,2})")
    rows = []
    for qm in re.finditer(r"^(Q[1-4])\s*\|\s*" + AMOUNT_RE + r"\s*\|\s*" + AMOUNT_RE + r"\s*\|\s*" + AMOUNT_RE, text, re.M):
        rows.append({"quarter": qm.group(1), "amount_paid": float(parse_amount(qm.group(2)) or 0),
                     "tax_deducted": float(parse_amount(qm.group(3)) or 0), "tax_deposited": float(parse_amount(qm.group(4)) or 0)})
    f["quarterly_tds"] = rows
    tm = re.search(r"^Total\s*\|\s*" + AMOUNT_RE + r"\s*\|\s*" + AMOUNT_RE + r"\s*\|\s*" + AMOUNT_RE, text, re.M)
    f["total_amount_paid"] = parse_amount(tm.group(1)) if tm else (Decimal(str(sum(x["amount_paid"] for x in rows))) if rows else None)
    f["total_tds"] = parse_amount(tm.group(2)) if tm else (Decimal(str(sum(x["tax_deducted"] for x in rows))) if rows else None)
    required = ["deductor_name", "deductor_tan", "section", "total_amount_paid", "total_tds"]
    found = sum(1 for k in required if f.get(k) not in (None, ""))
    r.score(found, len(required))
    key = f["deductor_tan"] or normalize_key(f["deductor_name"])
    if f.get("total_tds") is not None:
        r.observations.append(obs("TDS", counterparty=f["deductor_name"], counterparty_tan=f["deductor_tan"], counterparty_key=key,
                                  section=(f["section"] or "").replace("-", ""), amount=f["total_amount_paid"], tax_deducted=f["total_tds"],
                                  reference="Form 16A · Total", confidence=r.confidence))
    return r


# --------------------------------------------------------------------------- Form 26AS

ROW26_SUMMARY = re.compile(r"^(\d+)\s*\|\s*([^|]+?)\s*\|\s*(" + TAN_RE + r")\s*\|\s*([0-9A-Z\-]+)\s*\|\s*" + AMOUNT_RE + r"\s*\|\s*" + AMOUNT_RE + r"\s*\|\s*" + AMOUNT_RE + r"\s*$", re.M)
ROW26_TXN = re.compile(r"^\s*(\d+)\s*\|\s*([0-9A-Z\-]+)\s*\|\s*(\d{2}-[A-Za-z]{3}-\d{4})\s*\|\s*([A-Z])\s*\|\s*(\d{2}-[A-Za-z]{3}-\d{4}|-)\s*\|\s*([^|]*)\|\s*" + AMOUNT_RE + r"\s*\|\s*" + AMOUNT_RE + r"\s*\|\s*" + AMOUNT_RE + r"\s*$", re.M)
ROW26_PAY = re.compile(r"^(\d+)\s*\|\s*(\d{4})\s*\|\s*(\d{3})\s*\|\s*" + AMOUNT_RE + r"\s*\|\s*" + AMOUNT_RE + r"\s*\|\s*" + AMOUNT_RE + r"\s*\|\s*" + AMOUNT_RE + r"\s*\|\s*" + AMOUNT_RE + r"\s*\|\s*(\d+)\s*\|\s*(\d{2}-[A-Za-z]{3}-\d{4})\s*\|\s*(\d+)\s*$", re.M)


def extract_form26as(text: str) -> ExtractionResult:
    r = ExtractionResult()
    f = r.fields
    f["pan"] = find_text(text, r"\(PAN\):\s*(" + PAN_RE + ")")
    f["assessment_year"] = find_text(text, r"Assessment Year:\s*(\d{4}-\d{2})")
    f["financial_year"] = find_text(text, r"Financial Year:\s*(\d{4}-\d{2})")
    part1 = text.split("PART-II")[0]
    deductors = []
    for m in ROW26_SUMMARY.finditer(part1):
        deductors.append({"name": m.group(2).strip(), "tan": m.group(3), "section": m.group(4).replace("-", ""),
                          "amount_paid": float(parse_amount(m.group(5)) or 0), "tax_deducted": float(parse_amount(m.group(6)) or 0),
                          "tds_deposited": float(parse_amount(m.group(7)) or 0), "transactions": []})
    # nested transactions follow their deductor summary row
    current = None
    for line in part1.splitlines():
        sm = ROW26_SUMMARY.match(line)
        if sm:
            current = next((d for d in deductors if d["tan"] == sm.group(3) and d["section"] == sm.group(4).replace("-", "")), None)
            continue
        tm = ROW26_TXN.match(line)
        if tm and current is not None:
            current["transactions"].append({"section": tm.group(2).replace("-", ""), "date": (parse_date(tm.group(3)) or "").isoformat() if parse_date(tm.group(3)) else tm.group(3),
                                            "status": tm.group(4), "amount_paid": float(parse_amount(tm.group(7)) or 0),
                                            "tax_deducted": float(parse_amount(tm.group(8)) or 0), "tds_deposited": float(parse_amount(tm.group(9)) or 0)})
    f["deductors"] = deductors
    payments = []
    part3 = text.split("PART-III")[1] if "PART-III" in text else ""
    for m in ROW26_PAY.finditer(part3):
        payments.append({"major_head": m.group(2), "minor_head": m.group(3), "total": float(parse_amount(m.group(8)) or 0),
                         "bsr_code": m.group(9), "date": parse_date(m.group(10)).isoformat(), "challan": m.group(11)})
    f["tax_payments"] = payments
    for d in deductors:
        if d["transactions"] and abs(sum(t["tax_deducted"] for t in d["transactions"]) - d["tax_deducted"]) > 1:
            r.flags.append(f"Transaction rows for {d['name']} do not add up to the summary total.")
    found = sum(1 for k in ["pan", "assessment_year"] if f.get(k)) + (1 if deductors or "No Transactions Present" in part1 else 0)
    r.score(found, 3, penalty=0.05 * len(r.flags))
    r.period_label = f"FY {f['financial_year']}" if f.get("financial_year") else None
    for d in deductors:
        key = d["tan"]
        r.observations.append(obs("TDS", counterparty=d["name"], counterparty_tan=d["tan"], counterparty_key=key, section=d["section"],
                                  amount=d["amount_paid"], tax_deducted=d["tax_deducted"], reference=f"Part I · {d['name']} · {d['section']}", confidence=r.confidence))
        for t in d["transactions"]:
            r.observations.append(obs("TDS_TXN", counterparty=d["name"], counterparty_tan=d["tan"], counterparty_key=key, section=t["section"],
                                      amount=t["amount_paid"], tax_deducted=t["tax_deducted"], date=t["date"], status=t["status"],
                                      reference=f"Part I · {d['name']} · {t['date']}"))
    for p in payments:
        r.observations.append(obs("TAX_PAYMENT", amount=p["total"], date=p["date"], minor_head=p["minor_head"], challan=p["challan"], bsr_code=p["bsr_code"],
                                  reference=f"Part III · challan {p['challan']}", kind=("ADVANCE_TAX" if p["minor_head"] == "100" else "SELF_ASSESSMENT_TAX")))
    return r


# --------------------------------------------------------------------------- Interest certificate

ROW_INT = re.compile(r"^([A-Z0-9Xx\-]+)\s*\|\s*([^|]+?)\s*\|\s*" + AMOUNT_RE + r"\s*\|\s*" + AMOUNT_RE + r"\s*$", re.M)


def extract_interest_certificate(text: str) -> ExtractionResult:
    r = ExtractionResult()
    f = r.fields
    first_line = next((l.strip() for l in text.splitlines() if l.strip()), "")
    f["bank_name"] = find_text(text, r"^(.+?(?:Bank|Finance|Post Office|Co-?operative)[^\n]*)$", re.M | re.IGNORECASE) or first_line
    f["financial_year"] = find_text(text, r"Financial Year\s*(\d{4}-\d{2})")
    f["customer_pan"] = find_text(text, r"PAN:\s*(" + PAN_RE + ")")
    f["deductor_tan"] = find_text(text, r"TAN:\s*(" + TAN_RE + ")")
    rows = []
    for m in ROW_INT.finditer(text):
        acct, typ = m.group(1), m.group(2).strip()
        if acct.lower() == "total":
            continue
        kind = "SAVINGS" if "saving" in typ.lower() else ("RECURRING_DEPOSIT" if "recurring" in typ.lower() else ("FIXED_DEPOSIT" if "deposit" in typ.lower() else "OTHER"))
        rows.append({"account": mask_account(acct), "type": typ, "kind": kind, "interest": float(parse_amount(m.group(3)) or 0), "tds": float(parse_amount(m.group(4)) or 0)})
    f["rows"] = rows
    tm = re.search(r"^Total\s*\|\s*\|\s*" + AMOUNT_RE + r"\s*\|\s*" + AMOUNT_RE, text, re.M)
    f["total_interest"] = parse_amount(tm.group(1)) if tm else (Decimal(str(sum(x["interest"] for x in rows))) if rows else None)
    f["total_tds"] = parse_amount(tm.group(2)) if tm else (Decimal(str(sum(x["tds"] for x in rows))) if rows else None)
    if tm and rows and abs(sum(x["interest"] for x in rows) - float(f["total_interest"])) > 1:
        r.flags.append("Row totals do not match the certificate total.")
    found = (1 if f["bank_name"] else 0) + (1 if rows else 0) + (1 if f["financial_year"] else 0)
    r.score(found, 3, penalty=0.05 * len(r.flags))
    r.period_label = f"FY {f['financial_year']}" if f.get("financial_year") else None
    key = normalize_key(f["bank_name"])
    for row in rows:
        r.observations.append(obs("INTEREST", counterparty=f["bank_name"], counterparty_key=key, counterparty_tan=f["deductor_tan"], sub_kind=row["kind"],
                                  account_ref=row["account"], amount=row["interest"], tax_deducted=row["tds"],
                                  reference=f"Certificate · {row['account']} ({row['type']})", confidence=r.confidence))
    if f.get("total_tds") and float(f["total_tds"]) > 0:
        r.observations.append(obs("TDS", counterparty=f["bank_name"], counterparty_tan=f["deductor_tan"], counterparty_key=f["deductor_tan"] or key, section="194A",
                                  amount=f["total_interest"], tax_deducted=f["total_tds"], reference="Certificate · Total TDS", confidence=r.confidence))
    return r


# --------------------------------------------------------------------------- Salary slip


def extract_salary_slip(text: str) -> ExtractionResult:
    r = ExtractionResult()
    f = r.fields
    f["employer_name"] = next((l.strip() for l in text.splitlines() if l.strip()), None)
    f["month"] = find_text(text, r"for the month of\s+([A-Za-z]+\s+\d{4})")
    f["employee_pan"] = find_text(text, r"PAN:\s*(" + PAN_RE + ")")
    f["basic"] = find_amount(text, r"^Basic\s*\|")
    f["hra"] = find_amount(text, r"^HRA\s*\|")
    f["gross_earnings"] = find_amount(text, r"^Gross Earnings\s*\|")
    f["professional_tax"] = find_amount(text, r"Professional Tax\s*\|")
    f["tds"] = find_amount(text, r"Income Tax \(TDS\)\s*\|")
    f["provident_fund"] = find_amount(text, r"Provident Fund\s*\|")
    f["net_pay"] = find_amount(text, r"^Net Pay\s*\|")
    required = ["employer_name", "month", "gross_earnings", "tds"]
    found = sum(1 for k in required if f.get(k) not in (None, ""))
    r.score(found, len(required))
    r.period_label = f["month"]
    key = normalize_key(f["employer_name"])
    if f.get("gross_earnings") is not None:
        r.observations.append(obs("SALARY_MONTH", counterparty=f["employer_name"], counterparty_key=key, amount=f["gross_earnings"], tax_deducted=f["tds"] or 0,
                                  period=f["month"], basic=f["basic"], hra=f["hra"], professional_tax=f["professional_tax"], reference=f"Payslip · {f['month']}",
                                  confidence=r.confidence))
    return r


# --------------------------------------------------------------------------- Home loan certificate


def extract_home_loan_certificate(text: str) -> ExtractionResult:
    r = ExtractionResult()
    f = r.fields
    f["lender"] = next((l.strip() for l in text.splitlines() if l.strip()), None)
    f["financial_year"] = find_text(text, r"Financial Year\s*(\d{4}-\d{2})")
    f["loan_account"] = mask_account(find_text(text, r"Loan Account No\.?:\s*([A-Z0-9]+)"))
    f["property_address"] = find_text(text, r"Property Address:\s*([^\n]+)")
    f["principal_repaid"] = find_amount(text, r"Principal repaid during the year \(Rs\.\)")
    f["interest_paid"] = find_amount(text, r"Interest paid during the year \(Rs\.\)")
    f["sanction_date"] = (parse_date(find_text(text, r"Sanction date:\s*(\d{2}-[A-Za-z]{3}-\d{4})")) or None)
    if f["sanction_date"]:
        f["sanction_date"] = f["sanction_date"].isoformat()
    required = ["lender", "interest_paid", "principal_repaid"]
    found = sum(1 for k in required if f.get(k) not in (None, ""))
    r.score(found, len(required))
    r.period_label = f"FY {f['financial_year']}" if f.get("financial_year") else None
    if f.get("interest_paid") is not None:
        r.observations.append(obs("HOME_LOAN", counterparty=f["lender"], counterparty_key=normalize_key(f["lender"]), amount=f["interest_paid"],
                                  principal=f["principal_repaid"], account_ref=f["loan_account"], reference="Certificate · interest paid", confidence=r.confidence))
    return r


# --------------------------------------------------------------------------- Rent receipts


def extract_rent_receipts(text: str) -> ExtractionResult:
    r = ExtractionResult()
    f = r.fields
    months = []
    for m in re.finditer(r"sum of Rs\.?\s*" + AMOUNT_RE + r"\s+towards rent for the month of\s+([A-Za-z]+\s+\d{4})", text):
        months.append({"month": m.group(2), "amount": float(parse_amount(m.group(1)) or 0)})
    f["receipts"] = months
    f["total_rent_paid"] = Decimal(str(sum(x["amount"] for x in months))) if months else None
    f["landlord"] = find_text(text, r"Landlord:\s*([^\n,]+)")
    f["landlord_pan"] = find_text(text, r"PAN of Landlord:\s*(" + PAN_RE + ")")
    r.score(1 if months else 0, 1)
    if f["total_rent_paid"]:
        r.observations.append(obs("RENT_PAID", counterparty=f["landlord"], amount=f["total_rent_paid"], months=len(months), reference=f"{len(months)} receipts", confidence=r.confidence))
    return r
