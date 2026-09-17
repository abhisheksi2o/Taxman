"""Render realistic documents from a synthetic World (formats mirror what the extractors parse)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.documents.simple_pdf import text_pdf
from app.models.common import SourceType, money
from app.models.tax_model import SalaryIncome, TaxCase, TaxpayerProfile
from app.models.common import TracedValue
from app.tax_engine.engine import compute

from .world import Employer, World, month_date, month_end, month_label, quarter_end

ZERO = Decimal("0")


def amt(v: Decimal | int | float) -> str:
    """Indian-format amount with two decimals, e.g. 18,40,000.00."""
    d = money(v)
    neg = d < 0
    whole, frac = f"{abs(d):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    return ("-" if neg else "") + whole + "." + frac


def dmy(d: date) -> str:
    return d.strftime("%d-%b-%Y")


@dataclass
class GeneratedDocument:
    filename: str
    mime_type: str
    data: bytes
    doc_type: SourceType
    description: str


# --------------------------------------------------------------------------- employer TDS via the engine


def compute_employer_tds(world: World, emp: Employer, extra_80c: Decimal = ZERO) -> Decimal:
    """Employers compute TDS u/s 192 on the salary they pay, under the regime the employee opted for."""
    case = TaxCase(owner_user_id="synthetic", taxpayer=TaxpayerProfile(assessment_year=world.ay, date_of_birth=world.person.dob, city_type=world.person.city_type))
    exempt = emp.hra_exempt if emp.regime_with_employer == "OLD" else ZERO
    case.income.salary.append(SalaryIncome(employer_name=emp.name, employer_tan=emp.tan, gross_salary=TracedValue.of(emp.gross_annual, SourceType.FORM16),
                                           exempt_allowances=TracedValue.of(exempt, SourceType.FORM16), professional_tax=TracedValue.of(emp.pt_annual, SourceType.FORM16),
                                           employer_nps_contribution=TracedValue.of(emp.nps_annual, SourceType.FORM16)))
    comp = compute(case, as_of=world.rules.fy_end)
    total = comp.regime(emp.regime_with_employer).summary.total_tax_liability
    return money(total)


def split_quarters(total: Decimal, emp: Employer) -> list[Decimal]:
    per_month = total / Decimal(emp.months) if emp.months else ZERO
    qs = [ZERO, ZERO, ZERO, ZERO]
    for m in range(emp.from_month, emp.to_month + 1):
        qs[m // 3] += per_month
    qs = [money(q) for q in qs]
    diff = total - sum(qs, ZERO)
    for i in (3, 2, 1, 0):
        if qs[i] > 0:
            qs[i] += diff
            break
    return qs


# --------------------------------------------------------------------------- Form 16


def render_form16(world: World, emp: Employer, tds_override: Decimal | None = None) -> GeneratedDocument:
    p = world.person
    total_tds = tds_override if tds_override is not None else emp.tds_total
    qs = split_quarters(total_tds, emp)
    paid_q = split_quarters(emp.gross_annual, emp)
    std = world.rules.regime(emp.regime_with_employer).standard_deduction_salary
    exempt = emp.hra_exempt if emp.regime_with_employer == "OLD" else ZERO
    pt = emp.pt_annual if emp.regime_with_employer == "OLD" else ZERO
    lines = [
        "FORM NO. 16",
        "[See rule 31(1)(a)]",
        "PART A",
        "Certificate under section 203 of the Income-tax Act, 1961 for tax deducted at source on salary paid to an employee",
        f"Certificate No.: {emp.tan[:4]}{world.seed_str()}",
        f"Name and address of the Employer: {emp.name}, {emp.address}, {p.city}",
        f"Name and address of the Employee: {p.name}, {p.address}",
        f"PAN of the Deductor: {emp.pan}",
        f"TAN of the Deductor: {emp.tan}",
        f"PAN of the Employee: {p.pan}",
        f"Assessment Year: {world.ay}",
        f"Period with the Employer: From {dmy(month_date(world.rules, emp.from_month))} To {dmy(month_end(world.rules, emp.to_month))}",
        "Summary of amount paid/credited and tax deducted at source thereon in respect of the employee",
        "Quarter | Receipt Number | Amount paid/credited | Amount of tax deducted | Amount of tax deposited",
    ]
    for i in range(4):
        if paid_q[i] > 0:
            lines.append(f"Q{i + 1} | QRN{emp.tan[-3:]}{i + 1:02d}{world.seed_str()} | {amt(paid_q[i])} | {amt(qs[i])} | {amt(qs[i])}")
    lines.append(f"Total | | {amt(emp.gross_annual)} | {amt(total_tds)} | {amt(total_tds)}")
    perq = money(emp.gross_annual * Decimal("0.02"))
    lines += [
        "PART B (Annexure)",
        "Details of Salary Paid and any other income and tax deducted",
        f"Whether opting out of taxation u/s 115BAC(1A)?: {'Yes' if emp.regime_with_employer == 'OLD' else 'No'}",
        "1. Gross Salary",
        f"(a) Salary as per provisions contained in section 17(1): {amt(emp.gross_annual - perq)}",
        f"(b) Value of perquisites under section 17(2): {amt(perq)}",
        "(c) Profits in lieu of salary under section 17(3): 0.00",
        f"(d) Total: {amt(emp.gross_annual)}",
        "2. Less: Allowances to the extent exempt under section 10",
        f"(a) House rent allowance under section 10(13A): {amt(exempt)}",
        f"(h) Total amount of exemption claimed under section 10: {amt(exempt)}",
        f"3. Total amount of salary received from current employer [1(d)-2(h)]: {amt(emp.gross_annual - exempt)}",
        "4. Less: Deductions under section 16",
        f"(a) Standard deduction under section 16(ia): {amt(std)}",
        f"(c) Tax on employment under section 16(iii): {amt(pt)}",
        f"(d) Total amount of deductions under section 16: {amt(std + pt)}",
        f'5. Income chargeable under the head "Salaries" [(3+1(e)-4(d)]: {amt(emp.gross_annual - exempt - std - pt)}',
        "Annexure II - Salary break-up (as per payroll)",
        f"Basic Pay: {amt(emp.basic_annual)}",
        f"House Rent Allowance (received): {amt(emp.hra_annual)}",
        f"Special Allowance: {amt(emp.gross_annual - emp.basic_annual - emp.hra_annual - perq)}",
        f"Employer contribution to NPS under section 80CCD(2): {amt(emp.nps_annual)}",
        f"Total tax deducted: {amt(total_tds)}",
        "Verification: I, the undersigned, certify that the information given above is true, complete and correct.",
    ]
    fname = f"Form16_{emp.name.split()[0]}_{world.fy}.pdf"
    return GeneratedDocument(fname, "application/pdf", text_pdf(lines, "Form 16"), SourceType.FORM16, f"Form 16 from {emp.name}")


# --------------------------------------------------------------------------- Form 16A


def render_form16a(world: World, deductor: str, tan: str, section: str, nature: str, rows: list[tuple[date, Decimal, Decimal]]) -> GeneratedDocument:
    p = world.person
    lines = [
        "FORM NO. 16A",
        "[See rule 31(1)(b)]",
        "Certificate under section 203 of the Income-tax Act, 1961 for tax deducted at source",
        f"Certificate No.: {tan[:4]}A{world.seed_str()}",
        f"Name and address of the Deductor: {deductor}, {p.city}",
        f"Name and address of the Deductee: {p.name}, {p.address}",
        f"TAN of the Deductor: {tan}",
        f"PAN of the Deductee: {p.pan}",
        f"Assessment Year: {world.ay}",
        f"Period: From {dmy(world.rules.fy_start)} To {dmy(world.rules.fy_end)}",
        f"Nature of Payment: {nature} (Section {section})",
        "Details of tax deducted and deposited",
        "Quarter | Amount paid/credited | Amount of tax deducted | Amount of tax deposited",
    ]
    qtot = {}
    for d, paid, tds in rows:
        q = (d.month - 4) % 12 // 3
        a, t = qtot.get(q, (ZERO, ZERO))
        qtot[q] = (a + paid, t + tds)
    for q in sorted(qtot):
        a, t = qtot[q]
        lines.append(f"Q{q + 1} | {amt(a)} | {amt(t)} | {amt(t)}")
    tp = sum((r[1] for r in rows), ZERO)
    tt = sum((r[2] for r in rows), ZERO)
    lines.append(f"Total | {amt(tp)} | {amt(tt)} | {amt(tt)}")
    return GeneratedDocument(f"Form16A_{deductor.split()[0]}_{world.fy}.pdf", "application/pdf", text_pdf(lines, "Form 16A"), SourceType.FORM16A, f"Form 16A from {deductor} ({section})")


# --------------------------------------------------------------------------- Form 26AS


@dataclass
class TdsRow:
    deductor: str
    tan: str
    section: str
    transactions: list[tuple[date, Decimal, Decimal]]  # date, amount paid, tax deducted

    @property
    def paid(self) -> Decimal:
        return money(sum((t[1] for t in self.transactions), ZERO))

    @property
    def tds(self) -> Decimal:
        return money(sum((t[2] for t in self.transactions), ZERO))


def render_form26as(world: World, rows: list[TdsRow], payments: list[tuple[date, Decimal, str]] | None = None) -> GeneratedDocument:
    p = world.person
    lines = [
        "Form 26AS",
        "Annual Tax Statement under Section 203AA of the Income Tax Act, 1961",
        f"Permanent Account Number (PAN): {p.pan}",
        f"Financial Year: {world.fy}",
        f"Assessment Year: {world.ay}",
        f"Name of Assessee: {p.name.upper()}",
        f"Address of Assessee: {p.address}",
        "PART-I - Details of Tax Deducted at Source",
        "Sr. No. | Name of Deductor | TAN of Deductor | Section | Total Amount Paid/Credited | Total Tax Deducted | Total TDS Deposited",
    ]
    for i, r in enumerate(rows, start=1):
        lines.append(f"{i} | {r.deductor.upper()} | {r.tan} | {r.section} | {amt(r.paid)} | {amt(r.tds)} | {amt(r.tds)}")
        lines.append("  Sr. No. | Section | Transaction Date | Status of Booking | Date of Booking | Remarks | Amount Paid/Credited | Tax Deducted | TDS Deposited")
        for j, (d, paid, tds) in enumerate(r.transactions, start=1):
            lines.append(f"  {j} | {r.section} | {dmy(d)} | F | {dmy(d + timedelta(days=20))} | - | {amt(paid)} | {amt(tds)} | {amt(tds)}")
    if not rows:
        lines.append("No Transactions Present")
    lines += ["PART-II - Details of Tax Collected at Source", "No Transactions Present", "PART-III - Details of Tax Paid (other than TDS or TCS)"]
    if payments:
        lines.append("Sr. No. | Major Head | Minor Head | Tax | Surcharge | Cess | Interest | Total | BSR Code | Date of Deposit | Challan Serial No.")
        for i, (d, total, kind) in enumerate(payments, start=1):
            minor = "100" if kind == "ADVANCE_TAX" else "300"
            lines.append(f"{i} | 0021 | {minor} | {amt(total)} | 0.00 | 0.00 | 0.00 | {amt(total)} | 0004329 | {dmy(d)} | {10000 + i}")
    else:
        lines.append("No Transactions Present")
    lines += ["PART-IV - Details of Paid Refund", "No Transactions Present"]
    return GeneratedDocument(f"Form26AS_{world.fy}.pdf", "application/pdf", text_pdf(lines, "Form 26AS"), SourceType.FORM26AS, "Form 26AS (annual tax statement)")


# --------------------------------------------------------------------------- AIS / TIS


def render_ais(world: World, information: list[dict], tds: list[dict]) -> GeneratedDocument:
    doc = {"document": "Annual Information Statement", "pan": world.person.pan, "name": world.person.name, "assessment_year": world.ay, "financial_year": world.fy,
           "generated_on": world.rules.fy_end.isoformat(), "information": information, "tds_tcs": tds}
    return GeneratedDocument(f"AIS_{world.fy}.json", "application/json", json.dumps(doc, indent=2, default=str).encode(), SourceType.AIS, "Annual Information Statement export")


def render_tis(world: World, categories: list[dict]) -> GeneratedDocument:
    doc = {"document": "Taxpayer Information Summary", "pan": world.person.pan, "assessment_year": world.ay, "financial_year": world.fy, "categories": categories}
    return GeneratedDocument(f"TIS_{world.fy}.json", "application/json", json.dumps(doc, indent=2, default=str).encode(), SourceType.TIS, "Taxpayer Information Summary")


# --------------------------------------------------------------------------- Bank statement


def render_bank_statement(world: World, acct, txns: list[tuple[date, str, str, Decimal, Decimal]]) -> GeneratedDocument:
    """txns: (date, narration, ref, debit, credit) – already sorted by date."""
    p = world.person
    lines = [
        f"{acct.bank},,,,,",
        f"Statement of Account,,,,,",
        f"Account Holder: {p.name},,,,,",
        f"Account Number: {acct.number},Account Type: {acct.account_type.title()} Account,,,,",
        f"Statement Period: {dmy(world.rules.fy_start)} to {dmy(world.rules.fy_end + timedelta(days=1))},,,,,",
        "Date,Narration,Ref No,Debit,Credit,Balance",
    ]
    bal = acct.opening_balance
    for d, narration, ref, debit, credit in txns:
        bal = bal - debit + credit
        lines.append(f"{d.isoformat()},\"{narration}\",{ref},{f'{debit:.2f}' if debit else ''},{f'{credit:.2f}' if credit else ''},{bal:.2f}")
    return GeneratedDocument(f"BankStatement_{acct.bank.split()[0]}_{world.fy}.csv", "text/csv", "\n".join(lines).encode(), SourceType.BANK_STATEMENT,
                             f"Bank statement – {acct.bank} ({acct.number[-4:]})")


# --------------------------------------------------------------------------- Interest certificate


def render_interest_certificate(world: World, acct, savings_total: Decimal | None = None, fd_rows: list[tuple[str, Decimal, Decimal]] | None = None) -> GeneratedDocument:
    p = world.person
    lines = [
        f"{acct.bank}",
        f"Interest Certificate for the Financial Year {world.fy} (Assessment Year {world.ay})",
        f"Customer Name: {p.name}",
        f"PAN: {p.pan}",
        f"TAN: {acct.tan}",
        "Account / Deposit No. | Type | Interest Paid / Accrued (Rs.) | TDS Deducted (Rs.)",
    ]
    total_i = ZERO
    total_t = ZERO
    sv = acct.savings_total if savings_total is None else savings_total
    if sv > 0:
        lines.append(f"XXXXXX{acct.number[-4:]} | Savings Account | {amt(sv)} | 0.00")
        total_i += sv
    for number, interest, tds in (fd_rows if fd_rows is not None else [(f.number, f.interest, f.tds) for f in acct.fds]):
        lines.append(f"{number} | Fixed Deposit | {amt(interest)} | {amt(tds)}")
        total_i += interest
        total_t += tds
    lines.append(f"Total | | {amt(total_i)} | {amt(total_t)}")
    lines.append("This is a computer generated certificate and does not require a signature.")
    return GeneratedDocument(f"InterestCertificate_{acct.bank.split()[0]}_{world.fy}.pdf", "application/pdf", text_pdf(lines, "Interest Certificate"),
                             SourceType.INTEREST_CERTIFICATE, f"Interest certificate – {acct.bank}")


# --------------------------------------------------------------------------- Payslip


def render_payslip(world: World, emp: Employer, month_index: int) -> GeneratedDocument:
    p = world.person
    tds_m = money(emp.tds_total / Decimal(emp.months)) if emp.months else ZERO
    gross = emp.basic_monthly + emp.hra_monthly + emp.special_monthly
    ded = emp.pf_monthly + emp.pt_monthly + tds_m
    lines = [
        f"{emp.name}",
        f"PAYSLIP for the month of {month_label(world.rules, month_index)}",
        f"Employee Name: {p.name} | Employee Code: E{world.seed_str()} | PAN: {p.pan}",
        "Earnings | Amount | Deductions | Amount",
        f"Basic | {amt(emp.basic_monthly)} | Provident Fund | {amt(emp.pf_monthly)}",
        f"HRA | {amt(emp.hra_monthly)} | Professional Tax | {amt(emp.pt_monthly)}",
        f"Special Allowance | {amt(emp.special_monthly)} | Income Tax (TDS) | {amt(tds_m)}",
        f"Gross Earnings | {amt(gross)} | Total Deductions | {amt(ded)}",
        f"Net Pay | {amt(gross - ded)}",
    ]
    return GeneratedDocument(f"Payslip_{month_date(world.rules, month_index).strftime('%b%Y')}.pdf", "application/pdf", text_pdf(lines, "Payslip"), SourceType.SALARY_SLIP,
                             f"Payslip – {month_label(world.rules, month_index)}")


# --------------------------------------------------------------------------- Broker / dividend


def render_broker_statement(world: World, trades, broker: str = "Zephyr Securities") -> GeneratedDocument:
    lines = [f"{broker},,,,,,,,,", f"Tax P&L Statement FY {world.fy},,,,,,,,,", "Symbol,ISIN,Asset Type,Quantity,Buy Date,Buy Value,Sell Date,Sell Value,Charges,Trade Ref"]
    for t in trades:
        lines.append(f"{t.symbol},{t.isin},{t.asset_type},{t.quantity},{t.buy_date.isoformat()},{t.buy_value:.2f},{t.sell_date.isoformat()},{t.sell_value:.2f},{t.charges:.2f},{t.ref}")
    return GeneratedDocument(f"BrokerTaxPnL_{world.fy}.csv", "text/csv", "\n".join(lines).encode(), SourceType.BROKER_STATEMENT, f"Broker tax P&L – {broker}")


def render_dividend_statement(world: World, dividends, broker: str = "Zephyr Securities") -> GeneratedDocument:
    lines = [f"{broker},,,,,,,", f"Dividend Statement FY {world.fy},,,,,,,", "Symbol,ISIN,Payment Date,Quantity,Dividend Per Share,Gross Dividend,TDS,Net Amount"]
    for d in dividends:
        lines.append(f"{d.company},{d.isin},{d.paid_on.isoformat()},{d.quantity},{d.dps:.2f},{d.gross:.2f},{d.tds:.2f},{(d.gross - d.tds):.2f}")
    return GeneratedDocument(f"DividendStatement_{world.fy}.csv", "text/csv", "\n".join(lines).encode(), SourceType.DIVIDEND_STATEMENT, f"Dividend statement – {broker}")


# --------------------------------------------------------------------------- Home loan / rent / ITR / investments


def render_home_loan_certificate(world: World, loan) -> GeneratedDocument:
    lines = [
        f"{loan.lender}",
        f"Provisional Home Loan Interest Certificate - Financial Year {world.fy}",
        f"Borrower: {world.person.name}",
        f"PAN: {world.person.pan}",
        f"Loan Account No.: {loan.account}",
        f"Property Address: {loan.self_occupied_address or world.person.address}",
        f"Principal repaid during the year (Rs.): {amt(loan.principal)}",
        f"Interest paid during the year (Rs.): {amt(loan.interest)}",
        f"Sanction date: {dmy(loan.sanction_date)}",
        "This certificate is issued for the purpose of claiming deductions under the Income-tax Act, 1961.",
    ]
    return GeneratedDocument(f"HomeLoanCertificate_{world.fy}.pdf", "application/pdf", text_pdf(lines, "Home Loan Interest Certificate"), SourceType.HOME_LOAN_CERTIFICATE,
                             f"Home-loan interest certificate – {loan.lender}")


def render_rent_receipts(world: World, monthly_rent: Decimal, landlord: str, landlord_pan: str) -> GeneratedDocument:
    lines = []
    for m in range(12):
        lines += ["RENT RECEIPT", f"Received from {world.person.name} the sum of Rs. {amt(monthly_rent)} towards rent for the month of {month_label(world.rules, m)} for the premises at {world.person.address}.",
                  f"Landlord: {landlord}, PAN of Landlord: {landlord_pan}", f"Date: {dmy(month_date(world.rules, m, 5))}", ""]
    return GeneratedDocument(f"RentReceipts_{world.fy}.txt", "text/plain", "\n".join(lines).encode(), SourceType.RENT_RECEIPT, "Rent receipts (12 months)")


def render_previous_itr(world: World, prev: dict) -> GeneratedDocument:
    doc = {"document": "ITR-V Acknowledgement / return summary", **prev}
    return GeneratedDocument(f"ITR_{prev['assessment_year']}.json", "application/json", json.dumps(doc, indent=2, default=str).encode(), SourceType.PREVIOUS_ITR,
                             f"Previous return – AY {prev['assessment_year']}")


def render_investment_proof(world: World, investments) -> GeneratedDocument:
    doc = {"document": "Investment proof summary", "financial_year": world.fy,
           "items": [{"instrument": i.instrument, "provider": i.provider, "amount": float(i.amount), "date": i.date.isoformat(), "section": i.section} for i in investments]}
    return GeneratedDocument(f"InvestmentProofs_{world.fy}.json", "application/json", json.dumps(doc, indent=2).encode(), SourceType.INVESTMENT_PROOF, "Investment proofs (80C/80D)")


def seed_str(self) -> str:  # attached to World for certificate numbers (deterministic across processes)
    import hashlib

    return f"{int(hashlib.sha1(f'{self.person.pan}{self.ay}'.encode()).hexdigest()[:8], 16) % 100000:05d}"


World.seed_str = seed_str  # type: ignore[attr-defined]
