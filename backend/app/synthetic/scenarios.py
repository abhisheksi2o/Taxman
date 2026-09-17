"""Demo scenarios. Each builds a coherent World, renders documents, records what the taxpayer has already
entered, and registers *hidden issues* the reconciliation engine is expected to find."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.documents.extractors.common import normalize_key
from app.models.common import SourceType, TracedValue, ValueStatus, money
from app.models.tax_model import (
    BankAccount,
    BusinessIncome,
    Deduction,
    InterestIncome,
    RentalIncome,
    TaxCase,
    TaxpayerProfile,
)
from app.reconciliation import impact as imp

from .render import (
    GeneratedDocument,
    TdsRow,
    amt,
    compute_employer_tds,
    render_ais,
    render_bank_statement,
    render_broker_statement,
    render_dividend_statement,
    render_form16,
    render_form16a,
    render_form26as,
    render_home_loan_certificate,
    render_interest_certificate,
    render_investment_proof,
    render_payslip,
    render_previous_itr,
    render_rent_receipts,
    render_tis,
    split_quarters,
)
from .world import STOCKS, BankAccountW, Employer, World, WorldBuilder, month_date, quarter_end, tan_for

ZERO = Decimal("0")


@dataclass
class HiddenIssue:
    id: str
    kind: str
    category: str
    severity: str
    title: str
    description: str
    correct_answer: str
    expected_evidence: list[str]
    match: dict
    amount: float
    fix: Callable[[TaxCase], None] | None = None
    impact_mode: str = "TAX_DELTA"  # TAX_DELTA | CREDIT_DIFFERENCE | NONE
    expected_impact: float | None = None

    def public(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "fix"}


@dataclass
class GeneratedCase:
    scenario: str
    label: str
    description: str
    seed: int
    assessment_year: str
    world: World
    profile: TaxpayerProfile
    documents: list[GeneratedDocument] = field(default_factory=list)
    user_entries: list[Callable[[TaxCase], None]] = field(default_factory=list)
    hidden_issues: list[HiddenIssue] = field(default_factory=list)
    story: list[str] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    bank_accounts: list[BankAccount] = field(default_factory=list)


# ============================================================================ assembler helpers


class Assembler:
    """Derives downstream documents (26AS, AIS, TIS, bank statements) from the world."""

    def __init__(self, world: World, builder: WorldBuilder):
        self.w = world
        self.b = builder
        self.ais_counter = 90
        self.txn_counter = 1800

    def next_ais_id(self) -> str:
        self.ais_counter += 1
        return f"A{self.ais_counter}"

    def next_txn(self) -> str:
        self.txn_counter += 1
        return str(self.txn_counter)

    # ---- TDS rows (26AS) ------------------------------------------------------------------
    def tds_rows(self, tds_overrides: dict[str, Decimal] | None = None, exclude_tans: set[str] | None = None) -> list[TdsRow]:
        w = self.w
        rows: list[TdsRow] = []
        ov = tds_overrides or {}
        ex = exclude_tans or set()
        for emp in w.employers:
            if emp.tan in ex:
                continue
            total = ov.get(emp.tan, emp.tds_total)
            qs = split_quarters(total, emp)
            paid = split_quarters(emp.gross_annual, emp)
            rows.append(TdsRow(emp.name, emp.tan, "192", [(quarter_end(w.rules, q), paid[q], qs[q]) for q in range(4) if paid[q] > 0]))
        for acct in w.banks:
            if acct.tan in ex:
                continue
            txns = []
            for fd in acct.fds:
                if fd.tds > 0:
                    qi = money(fd.interest / 4)
                    qt = money(fd.tds / 4)
                    for i, d in enumerate(fd.credit_dates):
                        ii = qi if i < 3 else fd.interest - qi * 3
                        tt = qt if i < 3 else fd.tds - qt * 3
                        txns.append((d, ii, tt))
            if txns:
                rows.append(TdsRow(acct.bank, acct.tan, "194A", sorted(txns)))
        for dv in w.dividends:
            if dv.tds > 0:
                rows.append(TdsRow(dv.company, company_tan(dv.isin), "194", [(dv.paid_on, dv.gross, dv.tds)]))
        for cl in w.clients:
            if cl.tan in ex:
                continue
            rows.append(TdsRow(cl.name, cl.tan, "194J", [(quarter_end(w.rules, q), cl.receipts_quarters[q], money(cl.receipts_quarters[q] * cl.tds_rate)) for q in range(4)]))
        for p in w.properties:
            if p.tenant_tds_total > 0:
                rows.append(TdsRow(p.tenant, p.tenant_tan or "MUMT12345T", "194IB", [(w.rules.fy_end - timedelta(days=15), money(p.monthly_rent * p.months), p.tenant_tds_total)]))
        return rows

    # ---- AIS -------------------------------------------------------------------------------
    def ais(self, tds_rows: list[TdsRow], amount_overrides: dict[str, Decimal] | None = None, exclude_keys: set[str] | None = None) -> tuple[list[dict], list[dict], dict[str, str]]:
        w = self.w
        ov = amount_overrides or {}
        ex = exclude_keys or set()
        info: list[dict] = []
        ids: dict[str, str] = {}

        def add(key: str, **item) -> None:
            if key in ex:
                return
            i = self.next_ais_id()
            ids[key] = i
            item["amount"] = float(ov.get(key, item["amount"]))
            info.append({"id": i, "feedback_status": "Not submitted", **item})

        for emp in w.employers:
            add(f"salary:{emp.tan}", category="Salary", source=emp.name, source_tan=emp.tan, amount=emp.gross_annual, tds=emp.tds_total, period=w.fy)
        for acct in w.banks:
            if acct.savings_total > 0:
                add(f"sbint:{acct.number}", category="Interest from savings bank", source=acct.bank, source_tan=acct.tan, account=f"XXXXXX{acct.number[-4:]}", amount=acct.savings_total, tds=0, period=w.fy)
            for fd in acct.fds:
                add(f"fdint:{fd.number}", category="Interest from deposit", source=acct.bank, source_tan=acct.tan, account=fd.number, amount=fd.interest, tds=fd.tds, period=w.fy)
        for dv in w.dividends:
            add(f"div:{dv.isin}", category="Dividend", source=dv.company, source_tan=company_tan(dv.isin), isin=dv.isin, amount=dv.gross, tds=dv.tds, date=dv.paid_on.isoformat())
        for t in w.trades:
            add(f"sale:{t.ref}", category="Sale of securities and units of mutual fund", source="Zephyr Securities (depository)", isin=t.isin, sub_kind=t.asset_type,
                quantity=float(t.quantity), amount=t.sell_value, date=t.sell_date.isoformat(), symbol=t.symbol)
        for cl in w.clients:
            add(f"fees:{cl.tan}", category="Receipts from professional fees", source=cl.name, source_tan=cl.tan, amount=cl.total, tds=cl.tds_total, period=w.fy)
        for p in w.properties:
            if p.tenant_tds_total > 0:
                add(f"rent:{p.name}", category="Rent received", source=p.tenant, amount=money(p.monthly_rent * p.months), tds=p.tenant_tds_total, period=w.fy)
        tds = [{"section": r.section, "deductor": r.deductor, "tan": r.tan, "amount_paid": float(r.paid), "tax_deducted": float(r.tds), "quarter": "Q1-Q4"} for r in tds_rows]
        return info, tds, ids

    def tis(self, information: list[dict]) -> list[dict]:
        totals: dict[str, float] = {}
        for it in information:
            totals[it["category"]] = round(totals.get(it["category"], 0.0) + float(it["amount"]), 2)
        return [{"category": k, "processed_value": v, "derived_value": v} for k, v in totals.items()]

    # ---- bank statement transactions -----------------------------------------------------------
    def bank_txns(self, acct: BankAccountW, salary_from: list[Employer] | None = None, rent_from=None, dividends=None,
                  duplicate_first_interest: bool = False, next_fy_fd_interest: Decimal | None = None) -> tuple[list, dict[str, str]]:
        w = self.w
        rng = self.b.rng
        txns = []
        refs: dict[str, str] = {}
        for emp in (salary_from or []):
            net = emp.basic_monthly + emp.hra_monthly + emp.special_monthly - emp.pf_monthly - emp.pt_monthly - money(emp.tds_total / Decimal(emp.months))
            for m in range(emp.from_month, emp.to_month + 1):
                txns.append((month_date(w.rules, m, 1), f"SALARY {emp.name.split()[0].upper()} {month_date(w.rules, m).strftime('%b%y').upper()}", self.next_txn(), ZERO, money(net)))
        for i, (d, a) in enumerate(acct.savings_interest):
            start = quarter_end(w.rules, i) - timedelta(days=89)
            ref = self.next_txn()
            refs[f"sbint:{i}"] = ref
            txns.append((d, f"INT.PD:SB:{start.strftime('%d-%m-%Y')} TO {d.strftime('%d-%m-%Y')}", ref, ZERO, a))
            if duplicate_first_interest and i == 0:
                dup_ref = self.next_txn()
                refs["sbint:dup"] = dup_ref
                txns.append((d, f"INT.PD:SB:{start.strftime('%d-%m-%Y')} TO {d.strftime('%d-%m-%Y')}", dup_ref, ZERO, a))
        for fd in acct.fds:
            qi = money(fd.interest / 4)
            qt = money(fd.tds / 4)
            for i, d in enumerate(fd.credit_dates):
                ii = qi if i < 3 else fd.interest - qi * 3
                tt = qt if i < 3 else fd.tds - qt * 3
                ref = self.next_txn()
                refs[f"fdint:{fd.number}:{i}"] = ref
                txns.append((d, f"FD INT CR {fd.number} (NET OF TDS)", ref, ZERO, money(ii - tt)))
        if next_fy_fd_interest is not None and acct.fds:
            ref = self.next_txn()
            refs["fdint:nextfy"] = ref
            txns.append((w.rules.fy_end + timedelta(days=1), f"FD INT CR {acct.fds[0].number} (NET OF TDS)", ref, ZERO, next_fy_fd_interest))
        if rent_from is not None:
            for m in range(rent_from.months):
                ref = self.next_txn()
                refs[f"rent:{m}"] = ref
                credit = rent_from.monthly_rent - (money(rent_from.tenant_tds_total / rent_from.months) if rent_from.tenant_tds_total else ZERO)
                txns.append((month_date(w.rules, m, 3), f"NEFT CR-{rent_from.tenant.upper()}-RENT {month_date(w.rules, m).strftime('%b').upper()}", ref, ZERO, money(credit)))
        for dv in (dividends or []):
            ref = self.next_txn()
            refs[f"div:{dv.isin}"] = ref
            txns.append((dv.paid_on, f"ACH C- {dv.company.upper()} DIV", ref, ZERO, money(dv.gross - dv.tds)))
        # ordinary spending
        for m in range(12):
            txns.append((month_date(w.rules, m, rng.randint(4, 9)), "UPI-GROCERY MART", self.next_txn(), Decimal(rng.randint(3200, 9800)), ZERO))
            txns.append((month_date(w.rules, m, rng.randint(10, 20)), "CARD PAYMENT-CREDIT CARD BILL", self.next_txn(), Decimal(rng.randint(12000, 48000)), ZERO))
            if acct.is_primary and w.employers and not w.properties:
                txns.append((month_date(w.rules, m, 2), "NEFT DR-HOUSE RENT", self.next_txn(), Decimal(25000), ZERO))
        txns.sort(key=lambda t: (t[0], int(t[2])))
        return txns, refs


def company_tan(isin: str) -> str:
    digits = "".join(ch for ch in isin if ch.isdigit())[:5].ljust(5, "7")
    return f"MUM{isin[2]}{digits}C"


# ============================================================================ scenario context


class Ctx:
    def __init__(self, seed: int, ay: str):
        self.b = WorldBuilder(seed, ay)
        self.seed = seed
        self.ay = ay
        self.world: World | None = None
        self.asm: Assembler | None = None
        self.docs: list[GeneratedDocument] = []
        self.user_entries: list[Callable[[TaxCase], None]] = []
        self.hidden: list[HiddenIssue] = []
        self.story: list[str] = []
        self.questions: list[dict] = []
        self.bank_accounts: list[BankAccount] = []
        self.profile_overrides: dict = {}
        self._hid = 0

    def world_with(self, **kw) -> World:
        self.world = World(rules=self.b.rules, person=self.b.person(kw.get("age")))
        self.asm = Assembler(self.world, self.b)
        return self.world

    def finalize_employer(self, emp: Employer) -> None:
        emp.tds_total = compute_employer_tds(self.world, emp)
        emp.tds_quarters = split_quarters(emp.tds_total, emp)
        self.world.employers.append(emp)

    def add_hidden(self, **kw) -> HiddenIssue:
        self._hid += 1
        h = HiddenIssue(id=f"HI-{self._hid:02d}", **kw)
        self.hidden.append(h)
        return h

    def bank_account_record(self, acct: BankAccountW) -> None:
        self.bank_accounts.append(BankAccount(bank_name=acct.bank, account_number_masked=f"XXXXXX{acct.number[-4:]}", ifsc=f"{acct.bank[:4].upper()}0{acct.number[-6:]}",
                                              is_primary_for_refund=acct.is_primary, status=ValueStatus.USER_ENTERED))

    def profile(self, income_sources: list[str], employment: str = "SALARIED", **kw) -> TaxpayerProfile:
        p = self.world.person
        prof = TaxpayerProfile(assessment_year=self.ay, name=p.name, pan=p.pan, date_of_birth=p.dob, taxpayer_type="INDIVIDUAL", residential_status="RESIDENT",
                               employment_status=employment, income_sources=income_sources, employer_count=kw.get("employer_count", len(self.world.employers) or None),
                               has_investments=kw.get("has_investments", bool(self.world.investments)), has_rental_income=kw.get("has_rental_income", bool(self.world.properties)),
                               property_count=(len(self.world.properties) or None), has_home_loan=bool(self.world.loans), has_foreign_income_or_assets=False,
                               filed_previous_return=bool(self.world.prev_itr), city_type=p.city_type, regime_preference=kw.get("regime_preference", "UNDECIDED"),
                               email=p.email, phone=p.phone, address=p.address, onboarding_completed=True)
        prof.field_status = {k: ValueStatus.USER_CONFIRMED for k in ("name", "pan", "date_of_birth", "residential_status", "employment_status", "income_sources")}
        return prof

    # ---- standard document bundle ----------------------------------------------------------
    def standard_docs(self, *, form16_for: list[Employer] | None = None, tds_overrides: dict | None = None, exclude_tans: set | None = None,
                      ais_overrides: dict | None = None, ais_exclude: set | None = None, certificates_for: list[BankAccountW] | None = None,
                      statements_for: list[BankAccountW] | None = None, payslips_for: Employer | None = None, broker: bool = False, dividend_statement: bool = False,
                      form16a_for_clients: list | None = None, loan_cert: bool = False, prev_itr: bool = False, investment_proof: bool = False, rent_receipts: tuple | None = None,
                      bank_kwargs: dict | None = None, include_26as: bool = True, include_ais: bool = True, include_tis: bool = True) -> dict:
        w, asm = self.world, self.asm
        refs: dict = {}
        for emp in (form16_for if form16_for is not None else w.employers):
            self.docs.append(render_form16(w, emp))
        rows = asm.tds_rows(tds_overrides, exclude_tans)
        if include_26as:
            self.docs.append(render_form26as(w, rows, w.advance_tax or None))
        info, tds, ais_ids = asm.ais(rows, ais_overrides, ais_exclude)
        refs["ais_ids"] = ais_ids
        if include_ais:
            self.docs.append(render_ais(w, info, tds))
        if include_tis:
            self.docs.append(render_tis(w, asm.tis(info)))
        for acct in (certificates_for or []):
            self.docs.append(render_interest_certificate(w, acct))
        bk = bank_kwargs or {}
        for acct in (statements_for or []):
            kwargs = dict(bk.get(acct.number, {}))
            if acct.is_primary:
                kwargs.setdefault("salary_from", w.employers)
                kwargs.setdefault("rent_from", w.properties[0] if w.properties else None)
                kwargs.setdefault("dividends", w.dividends)
            txns, trefs = asm.bank_txns(acct, **kwargs)
            refs[f"bank:{acct.number}"] = trefs
            self.docs.append(render_bank_statement(w, acct, txns))
        if payslips_for is not None:
            for m in range(payslips_for.from_month, min(payslips_for.from_month + 3, payslips_for.to_month + 1)):
                self.docs.append(render_payslip(w, payslips_for, m))
        if broker and w.trades:
            self.docs.append(render_broker_statement(w, w.trades))
        if dividend_statement and w.dividends:
            self.docs.append(render_dividend_statement(w, w.dividends))
        for cl in (form16a_for_clients or []):
            self.docs.append(render_form16a(w, cl.name, cl.tan, "194J", "Fees for professional or technical services",
                                            [(quarter_end(w.rules, q), cl.receipts_quarters[q], money(cl.receipts_quarters[q] * cl.tds_rate)) for q in range(4)]))
        if loan_cert:
            for loan in w.loans:
                self.docs.append(render_home_loan_certificate(w, loan))
        if prev_itr and w.prev_itr:
            self.docs.append(render_previous_itr(w, w.prev_itr))
        if investment_proof and w.investments:
            self.docs.append(render_investment_proof(w, w.investments))
        if rent_receipts:
            self.docs.append(render_rent_receipts(w, *rent_receipts))
        return refs

    def result(self, code: str, label: str, description: str, profile: TaxpayerProfile) -> GeneratedCase:
        return GeneratedCase(scenario=code, label=label, description=description, seed=self.seed, assessment_year=self.ay, world=self.world, profile=profile,
                             documents=self.docs, user_entries=self.user_entries, hidden_issues=self.hidden, story=self.story, questions=self.questions,
                             bank_accounts=self.bank_accounts)


def prev_itr_for(world: World, gti: Decimal, heads: dict[str, Decimal], regime: str = "NEW") -> dict:
    ay_prev = f"{int(world.ay[:4]) - 1}-{int(world.ay[:4]) % 100:02d}"
    return {"assessment_year": ay_prev, "itr_form": "ITR-1", "regime": regime, "gross_total_income": float(gti), "total_deductions": 0.0,
            "taxable_income": float(gti - Decimal(75000)), "total_tax": float(money(gti * Decimal("0.09"))), "tds": float(money(gti * Decimal("0.09"))),
            "refund_or_payable": 0.0, "income_heads": {k: float(v) for k, v in heads.items()}, "filed_on": (world.rules.fy_start - timedelta(days=250)).isoformat(),
            "acknowledgement_number": f"{abs(int(world.person.pan[5:9])) * 37:015d}"[:15]}


# ============================================================================ scenarios


def build_salaried_basic(ctx: Ctx) -> GeneratedCase:
    w = ctx.world_with()
    emp = ctx.b.employer(1840000, idx=0)
    ctx.finalize_employer(emp)
    acct = ctx.b.bank(0, primary=True, savings_annual=18420)
    w.banks.append(acct)
    ctx.bank_account_record(acct)
    w.prev_itr = prev_itr_for(w, Decimal(1720000), {"Salary": Decimal(1720000), "Other sources": Decimal(16800)})
    ctx.standard_docs(certificates_for=[acct], statements_for=[acct], payslips_for=emp, prev_itr=True)
    ctx.story += [f"Salary {amt(emp.gross_annual)} → Form 16 (TDS {amt(emp.tds_total)}) → 26AS → AIS",
                  f"Savings interest {amt(acct.savings_total)} → bank statement → interest certificate → AIS (SFT-016)",
                  "Control case: all sources agree – Astra should raise no HIGH/MEDIUM items."]
    ctx.questions += [{"question": "What income have I reported?", "must_mention": [amt(emp.gross_annual), amt(acct.savings_total)], "answer_key": "salary and savings interest with sources"},
                      {"question": "What information is missing?", "must_mention": [], "answer_key": "nothing material missing"}]
    return ctx.result("salaried_basic", "Salaried employee (clean)", "Single employer, one savings account, all sources consistent. Tests false positives.",
                      ctx.profile(["SALARY", "INTEREST"], regime_preference="NEW"))


def build_multiple_employers(ctx: Ctx) -> GeneratedCase:
    w = ctx.world_with()
    emp_a = ctx.b.employer(1500000, from_month=0, to_month=4, idx=0)
    emp_b = ctx.b.employer(2100000, from_month=5, to_month=11, idx=1)
    ctx.finalize_employer(emp_a)
    ctx.finalize_employer(emp_b)
    acct = ctx.b.bank(0, primary=True, savings_annual=12640)
    w.banks.append(acct)
    ctx.bank_account_record(acct)
    ctx.standard_docs(form16_for=[emp_a], certificates_for=[acct], statements_for=[acct])
    ctx.story += [f"Employer A {emp_a.name}: Apr–Aug salary {amt(emp_a.gross_annual)} → Form 16 uploaded",
                  f"Employer B {emp_b.name}: Sep–Mar salary {amt(emp_b.gross_annual)} → Form 16 NOT uploaded, but 26AS/AIS report it",
                  "Bank statement shows salary credits from both employers"]
    ctx.add_hidden(kind="MISSING", category="SALARY", severity="HIGH", title=f"Salary from {emp_b.name} not accounted for",
                   description=f"26AS and AIS report salary of {amt(emp_b.gross_annual)} with TDS {amt(emp_b.tds_total)} from a second employer; no Form 16 or salary entry exists.",
                   correct_answer=f"Add the second employment ({emp_b.name}) – gross {amt(emp_b.gross_annual)}, TDS {amt(emp_b.tds_total)} – and upload its Form 16.",
                   expected_evidence=["Part I", "AIS item"], match={"kind": "MISSING", "category": "SALARY", "key": normalize_key(emp_b.name), "tan": emp_b.tan},
                   amount=float(emp_b.gross_annual), fix=imp.add_salary(emp_b.name, emp_b.tan, emp_b.gross_annual, emp_b.tds_total))
    ctx.questions += [{"question": "What information is missing?", "must_mention": [emp_b.name.split()[0]], "answer_key": "second employer salary missing"},
                      {"question": "What documents are still required?", "must_mention": ["Form 16"], "answer_key": "Form 16 of the second employer"}]
    return ctx.result("multiple_employers", "Multiple employers", "Job change mid-year; only the first employer's Form 16 is uploaded.",
                      ctx.profile(["SALARY", "INTEREST"], employer_count=2))


def build_salaried_interest(ctx: Ctx) -> GeneratedCase:
    w = ctx.world_with()
    emp = ctx.b.employer(1640000, idx=2)
    ctx.finalize_employer(emp)
    acct = ctx.b.bank(0, primary=True, savings_annual=18420, fds=[(1200000, "0.0725")])
    w.banks.append(acct)
    ctx.bank_account_record(acct)
    refs = ctx.standard_docs(statements_for=[acct])  # no interest certificate, nothing declared
    fd = acct.fds[0]
    sb_ref = refs[f"bank:{acct.number}"]["sbint:0"]
    ctx.story += [f"Salary {amt(emp.gross_annual)} → Form 16 → 26AS → AIS",
                  f"Savings interest {amt(acct.savings_total)} credited quarterly (first credit Transaction #{sb_ref}) → AIS {refs['ais_ids'][f'sbint:{acct.number}']} – not declared",
                  f"FD {fd.number} interest {amt(fd.interest)} with TDS {amt(fd.tds)} → 26AS (194A) → AIS {refs['ais_ids'][f'fdint:{fd.number}']} – not declared"]
    ctx.add_hidden(kind="MISSING", category="INTEREST", severity="MEDIUM", title="Savings-account interest not accounted for",
                   description=f"Bank statement shows {amt(acct.savings_total)} of savings interest (AIS {refs['ais_ids'][f'sbint:{acct.number}']}) that is not in the return.",
                   correct_answer=f"Add savings interest of {amt(acct.savings_total)} from {acct.bank}; 80TTA (old regime) applies up to ₹10,000.",
                   expected_evidence=[f"Transaction #{sb_ref}", f"AIS item {refs['ais_ids'][f'sbint:{acct.number}']}"],
                   match={"kind": "MISSING", "category": "INTEREST", "key": normalize_key(acct.bank), "sub_kind": "SAVINGS"}, amount=float(acct.savings_total),
                   fix=imp.add_interest(acct.bank, acct.savings_total, "SAVINGS"))
    ctx.add_hidden(kind="MISSING", category="INTEREST", severity="HIGH", title="Fixed-deposit interest with TDS not accounted for",
                   description=f"26AS shows TDS of {amt(fd.tds)} u/s 194A by {acct.bank} on interest of {amt(fd.interest)}; no matching interest income exists.",
                   correct_answer=f"Add FD interest of {amt(fd.interest)} (TDS {amt(fd.tds)}) from {acct.bank}.",
                   expected_evidence=["194A", f"AIS item {refs['ais_ids'][f'fdint:{fd.number}']}"],
                   match={"kind": "MISSING", "category": "INTEREST", "key": normalize_key(acct.bank), "sub_kind": "FIXED_DEPOSIT"}, amount=float(fd.interest),
                   fix=imp.add_interest(acct.bank, fd.interest, "FIXED_DEPOSIT", tds=fd.tds))
    ctx.questions += [{"question": "What information is missing?", "must_mention": [amt(acct.savings_total), amt(fd.interest)], "answer_key": "savings and FD interest missing"},
                      {"question": "Why is there a mismatch?", "must_mention": ["194A"], "answer_key": "TDS in 26AS without matching income"}]
    return ctx.result("salaried_interest", "Salaried + interest", "Interest income visible in bank statement, 26AS and AIS but not yet declared.",
                      ctx.profile(["SALARY", "INTEREST"]))


def build_salaried_capital_gains(ctx: Ctx) -> GeneratedCase:
    w = ctx.world_with()
    emp = ctx.b.employer(2200000, idx=3)
    ctx.finalize_employer(emp)
    acct = ctx.b.bank(0, primary=True, savings_annual=14260)
    w.banks.append(acct)
    ctx.bank_account_record(acct)
    w.trades = ctx.b.trades(n_equity=3, long_term=1, funds=[2])  # FUNDS[2] is labelled just "MF"
    w.dividends = ctx.b.dividends(n=2)
    mismatch_trade = w.trades[1]
    ais_value = money(mismatch_trade.sell_value * Decimal("1.05"))
    refs = ctx.standard_docs(certificates_for=[acct], statements_for=[acct], broker=True, dividend_statement=True, ais_overrides={f"sale:{mismatch_trade.ref}": ais_value})
    mf_trade = next(t for t in w.trades if t.asset_type == "MF")
    ctx.story += [f"Broker trades → tax P&L → capital gains; AIS mirrors sale consideration per trade",
                  f"AIS reports {amt(ais_value)} for {mismatch_trade.symbol} vs broker {amt(mismatch_trade.sell_value)}",
                  f"{mf_trade.symbol} is labelled 'MF' only – equity vs debt classification changes the tax treatment"]
    ctx.add_hidden(kind="MISMATCH", category="CAPITAL_GAINS", severity="LOW", title=f"Sale value of {mismatch_trade.symbol} differs between AIS and broker statement",
                   description=f"AIS {refs['ais_ids'][f'sale:{mismatch_trade.ref}']} reports {amt(ais_value)}; the broker statement shows {amt(mismatch_trade.sell_value)}.",
                   correct_answer="Contract-note value from the broker is authoritative; submit AIS feedback if the AIS figure is wrong.",
                   expected_evidence=[f"AIS item {refs['ais_ids'][f'sale:{mismatch_trade.ref}']}", f"Trade {mismatch_trade.ref}"],
                   match={"kind": "MISMATCH", "category": "CAPITAL_GAINS", "key": mismatch_trade.isin}, amount=float(ais_value - mismatch_trade.sell_value), fix=None, impact_mode="NONE")
    ctx.add_hidden(kind="CLASSIFICATION", category="CAPITAL_GAINS", severity="MEDIUM", title=f"Fund type of {mf_trade.symbol} requires confirmation",
                   description="The statement labels the units 'MF' without an equity/debt category; short-term gains on equity funds are taxed at 20% but debt-fund gains at slab rates.",
                   correct_answer="Flexi-cap funds are equity-oriented → EQUITY_MF; held under 12 months → STCG u/s 111A at 20%.",
                   expected_evidence=[f"Trade {mf_trade.ref}"], match={"kind": "CLASSIFICATION", "category": "CAPITAL_GAINS", "key": mf_trade.isin}, amount=float(mf_trade.sell_value - mf_trade.buy_value),
                   fix=None, impact_mode="NONE")
    ctx.questions += [{"question": "Explain my capital gains.", "must_mention": [mismatch_trade.symbol], "answer_key": "per-trade gains with short/long classification"},
                      {"question": "Why is there a mismatch?", "must_mention": [mismatch_trade.symbol], "answer_key": "AIS vs broker sale value"}]
    return ctx.result("salaried_capital_gains", "Salaried + capital gains", "Equity trades and dividends; AIS differs from the broker on one trade and a fund needs classification.",
                      ctx.profile(["SALARY", "INTEREST", "CAPITAL_GAINS", "DIVIDEND"]))


def build_freelancer(ctx: Ctx) -> GeneratedCase:
    w = ctx.world_with()
    w.clients = ctx.b.clients([1400000, 900000, 620000])
    acct = ctx.b.bank(1, primary=True, savings_annual=9420)
    w.banks.append(acct)
    ctx.bank_account_record(acct)
    w.advance_tax = [(month_date(w.rules, 5, 14), Decimal(60000), "ADVANCE_TAX"), (month_date(w.rules, 8, 14), Decimal(60000), "ADVANCE_TAX"), (month_date(w.rules, 11, 14), Decimal(60000), "ADVANCE_TAX")]
    declared = w.clients[:2]
    missing = w.clients[2]
    refs = ctx.standard_docs(certificates_for=[acct], statements_for=[acct], form16a_for_clients=declared, bank_kwargs={acct.number: {"salary_from": []}})
    total_declared = sum((c.total for c in declared), ZERO)
    ctx.user_entries.append(lambda c, t=total_declared: c.income.business.append(BusinessIncome(description="Freelance software consulting", nature="PROFESSION_44ADA",
                                                                                                 gross_receipts=TracedValue.of(t, SourceType.USER_INPUT, reference="Entered during onboarding"), status=ValueStatus.USER_ENTERED)))
    ctx.story += [f"Three clients pay professional fees with 10% TDS u/s 194J → 26AS → AIS",
                  f"Taxpayer entered receipts of {amt(total_declared)} (clients 1 & 2); client 3 ({missing.name}, {amt(missing.total)}) is missing",
                  "Presumptive taxation u/s 44ADA – 50% of receipts deemed income"]
    ctx.add_hidden(kind="MISSING", category="BUSINESS", severity="HIGH", title=f"Professional receipts from {missing.name} not accounted for",
                   description=f"26AS shows TDS of {amt(missing.tds_total)} u/s 194J on {amt(missing.total)} paid by {missing.name}; declared receipts do not include it.",
                   correct_answer=f"Add receipts of {amt(missing.total)} from {missing.name} (TDS {amt(missing.tds_total)}).",
                   expected_evidence=["194J", f"AIS item {refs['ais_ids'][f'fees:{missing.tan}']}"], match={"kind": "MISSING", "category": "BUSINESS", "key": normalize_key(missing.name), "tan": missing.tan},
                   amount=float(missing.total), fix=imp.add_business_receipts(missing.total))
    ctx.questions += [{"question": "What information is missing?", "must_mention": [missing.name.split()[0]], "answer_key": "client 3 receipts"},
                      {"question": "Why is my tax higher this year?", "must_mention": [], "answer_key": "grounded explanation"}]
    return ctx.result("freelancer", "Freelancer (44ADA)", "Consultant with three clients; one client's fees (visible in 26AS/AIS) were not entered.",
                      ctx.profile(["FREELANCE", "INTEREST"], employment="SELF_EMPLOYED", employer_count=0))


def build_rental_income(ctx: Ctx) -> GeneratedCase:
    w = ctx.world_with()
    emp = ctx.b.employer(1950000, idx=4)
    ctx.finalize_employer(emp)
    prop = ctx.b.property(monthly_rent=62000, with_tds=True)
    w.properties.append(prop)
    acct = ctx.b.bank(2, primary=True, savings_annual=11380)
    w.banks.append(acct)
    ctx.bank_account_record(acct)
    refs = ctx.standard_docs(certificates_for=[acct], statements_for=[acct])
    annual = money(prop.monthly_rent * prop.months)
    ctx.story += [f"Tenant pays {amt(prop.monthly_rent)}/month → deducts TDS u/s 194-IB → 26AS → AIS 'Rent received'",
                  "Monthly NEFT rent credits in the bank statement",
                  "Taxpayer marked 'rental income: no' during onboarding"]
    ctx.add_hidden(kind="MISSING", category="RENTAL", severity="HIGH", title="Rental income not accounted for",
                   description=f"26AS shows TDS u/s 194-IB by {prop.tenant} on rent of {amt(annual)}; AIS and monthly bank credits agree. No house-property income is declared.",
                   correct_answer=f"Add let-out property income: rent {amt(annual)}, tenant TDS {amt(prop.tenant_tds_total)}; 30% standard deduction u/s 24(a) applies.",
                   expected_evidence=["194IB", f"AIS item {refs['ais_ids'][f'rent:{prop.name}']}"], match={"kind": "MISSING", "category": "RENTAL", "key": normalize_key(prop.tenant)},
                   amount=float(annual), fix=imp.add_rental(prop.name, annual, tenant_tds=prop.tenant_tds_total, tenant=prop.tenant))
    ctx.questions += [{"question": "What information is missing?", "must_mention": ["rent"], "answer_key": "rental income missing"},
                      {"question": "Show me everything contributing to my taxable income.", "must_mention": [amt(emp.gross_annual)], "answer_key": "salary + interest"}]
    return ctx.result("rental_income", "Rental income", "Salaried taxpayer with a let-out flat whose rent (with tenant TDS) is not declared.",
                      ctx.profile(["SALARY", "INTEREST"], has_rental_income=False))


def build_multiple_bank_accounts(ctx: Ctx) -> GeneratedCase:
    w = ctx.world_with()
    emp = ctx.b.employer(1580000, idx=1)
    ctx.finalize_employer(emp)
    a = ctx.b.bank(0, primary=True, savings_annual=16200, fds=[(900000, "0.07")])
    b = ctx.b.bank(1, savings_annual=7350)
    c = ctx.b.bank(2, savings_annual=9860)
    w.banks += [a, b, c]
    for acct in (a, b, c):
        ctx.bank_account_record(acct)
    refs = ctx.standard_docs(certificates_for=[a, b], statements_for=[a, c], bank_kwargs={c.number: {"salary_from": []}})
    fd = a.fds[0]
    ctx.user_entries.append(lambda case, bank=a.bank, fd=fd: case.income.interest.append(InterestIncome(payer_name=bank, kind="FIXED_DEPOSIT", account_ref=fd.number,
                                                                                                          amount=TracedValue.of(fd.interest, SourceType.USER_INPUT, reference="Entered manually"),
                                                                                                          tds=TracedValue.of(fd.tds, SourceType.USER_INPUT), status=ValueStatus.USER_ENTERED)))
    ctx.story += [f"Bank A {a.bank}: certificate (savings + FD) uploaded AND FD interest entered manually → duplicate",
                  f"Bank B {b.bank}: certificate uploaded",
                  f"Bank C {c.bank}: only a bank statement – savings interest {amt(c.savings_total)} not declared (AIS {refs['ais_ids'][f'sbint:{c.number}']})"]
    ctx.add_hidden(kind="DUPLICATE", category="INTEREST", severity="MEDIUM", title=f"FD interest from {a.bank} appears twice",
                   description=f"The interest certificate and a manual entry both report {amt(fd.interest)} for deposit {fd.number}.",
                   correct_answer="Keep the certificate entry and remove the manual entry.", expected_evidence=["Certificate", "Entered manually"],
                   match={"kind": "DUPLICATE", "category": "INTEREST", "key": normalize_key(a.bank)}, amount=float(fd.interest), fix=None, impact_mode="DUPLICATE")
    ctx.add_hidden(kind="MISSING", category="INTEREST", severity="MEDIUM", title=f"Savings interest from {c.bank} not accounted for",
                   description=f"Bank statement and AIS show {amt(c.savings_total)} of savings interest at {c.bank}.",
                   correct_answer=f"Add savings interest of {amt(c.savings_total)} from {c.bank}.",
                   expected_evidence=["Transaction #", f"AIS item {refs['ais_ids'][f'sbint:{c.number}']}"], match={"kind": "MISSING", "category": "INTEREST", "key": normalize_key(c.bank), "sub_kind": "SAVINGS"},
                   amount=float(c.savings_total), fix=imp.add_interest(c.bank, c.savings_total, "SAVINGS"))
    ctx.questions += [{"question": "What information is missing?", "must_mention": [c.bank.split()[0]], "answer_key": "bank C interest"},
                      {"question": "Why is there a mismatch?", "must_mention": ["twice"], "answer_key": "duplicate FD interest"}]
    return ctx.result("multiple_bank_accounts", "Multiple bank accounts", "Three banks: one duplicate entry and one undeclared account.",
                      ctx.profile(["SALARY", "INTEREST"]))


def build_tds_mismatch(ctx: Ctx) -> GeneratedCase:
    w = ctx.world_with()
    emp = ctx.b.employer(1840000, idx=0)
    ctx.finalize_employer(emp)
    acct = ctx.b.bank(0, primary=True, savings_annual=13840)
    w.banks.append(acct)
    ctx.bank_account_record(acct)
    short = Decimal(15000)
    deposited = emp.tds_total - short
    refs = ctx.standard_docs(certificates_for=[acct], statements_for=[acct], tds_overrides={emp.tan: deposited}, payslips_for=emp)
    ctx.story += [f"Form 16 certifies TDS of {amt(emp.tds_total)}; 26AS/AIS show only {amt(deposited)} deposited (Q4 short by {amt(short)})",
                  "Payslips show monthly TDS consistent with Form 16"]
    ctx.add_hidden(kind="MISMATCH", category="TDS", severity="HIGH", title=f"TDS reported by {emp.name} differs from Form 26AS",
                   description=f"Form 16 shows {amt(emp.tds_total)} but 26AS shows {amt(deposited)} – {amt(short)} of credit is not visible in 26AS.",
                   correct_answer=f"Credit is limited to 26AS ({amt(deposited)}) until the employer corrects its TDS return; ask the employer to revise Form 24Q for Q4.",
                   expected_evidence=["Form 16", "Form 26AS"], match={"kind": "MISMATCH", "category": "TDS", "key": normalize_key(emp.name), "section": "192"}, amount=float(short), fix=None,
                   impact_mode="CREDIT_DIFFERENCE", expected_impact=float(short))
    ctx.questions += [{"question": "Why is there a mismatch?", "must_mention": [amt(emp.tds_total), amt(deposited)], "answer_key": "Form 16 vs 26AS TDS"},
                      {"question": "Why is my refund different?", "must_mention": [amt(short)], "answer_key": "credit difference of 15,000"}]
    return ctx.result("tds_mismatch", "TDS mismatch", "Employer's Form 16 TDS exceeds what 26AS shows as deposited.", ctx.profile(["SALARY", "INTEREST"]))


def build_ais_discrepancy(ctx: Ctx) -> GeneratedCase:
    w = ctx.world_with()
    emp = ctx.b.employer(1720000, idx=5)
    ctx.finalize_employer(emp)
    acct = ctx.b.bank(3, primary=True, savings_annual=10120, fds=[(1100000, "0.0782")])
    w.banks.append(acct)
    ctx.bank_account_record(acct)
    w.dividends = ctx.b.dividends(symbols=[STOCKS[0], STOCKS[2]], n=2)
    dv = w.dividends[0]
    ais_div = money(dv.gross + Decimal(6000))
    fd = acct.fds[0]
    ais_fd = money(fd.interest + Decimal(6000))
    refs = ctx.standard_docs(certificates_for=[acct], statements_for=[acct], dividend_statement=True, ais_overrides={f"div:{dv.isin}": ais_div, f"fdint:{fd.number}": ais_fd})
    ctx.story += [f"Dividend statement shows {amt(dv.gross)} from {dv.company}; AIS shows {amt(ais_div)} (holding in another demat)",
                  f"Interest certificate shows FD interest {amt(fd.interest)} (paid); AIS shows {amt(ais_fd)} (accrued)"]
    ctx.add_hidden(kind="MISMATCH", category="DIVIDEND", severity="MEDIUM", title=f"Dividend from {dv.company} differs between AIS and the dividend statement",
                   description=f"AIS {refs['ais_ids'][f'div:{dv.isin}']} reports {amt(ais_div)} while the broker statement shows {amt(dv.gross)}.",
                   correct_answer="AIS includes a holding in a second demat account – report the AIS amount after checking that demat statement.",
                   expected_evidence=[f"AIS item {refs['ais_ids'][f'div:{dv.isin}']}", "Dividend"], match={"kind": "MISMATCH", "category": "DIVIDEND", "key": normalize_key(dv.company)},
                   amount=6000.0, fix=imp.add_dividend(dv.company, Decimal(6000)))
    ctx.add_hidden(kind="MISMATCH", category="INTEREST", severity="MEDIUM", title=f"FD interest from {acct.bank} differs between AIS and the certificate",
                   description=f"AIS {refs['ais_ids'][f'fdint:{fd.number}']} reports {amt(ais_fd)} (accrued) while the certificate shows {amt(fd.interest)} (paid).",
                   correct_answer="Report accrued interest as per AIS to stay consistent with the deductor's reporting (professional review recommended).",
                   expected_evidence=[f"AIS item {refs['ais_ids'][f'fdint:{fd.number}']}", "Certificate"], match={"kind": "MISMATCH", "category": "INTEREST", "key": normalize_key(acct.bank)},
                   amount=6000.0, fix=imp.add_interest(acct.bank, Decimal(6000), "FIXED_DEPOSIT"))
    ctx.questions += [{"question": "Why is there a mismatch?", "must_mention": [dv.company.split()[0]], "answer_key": "AIS vs statements"},
                      {"question": "What income have I reported?", "must_mention": [amt(fd.interest)], "answer_key": "salary, interest, dividends"}]
    return ctx.result("ais_discrepancy", "AIS discrepancy", "AIS reports higher dividend and FD interest than the taxpayer's own statements.",
                      ctx.profile(["SALARY", "INTEREST", "DIVIDEND"]))


def build_duplicate_transaction(ctx: Ctx) -> GeneratedCase:
    w = ctx.world_with()
    emp = ctx.b.employer(1490000, idx=2)
    ctx.finalize_employer(emp)
    acct = ctx.b.bank(0, primary=True, savings_annual=18420)
    w.banks.append(acct)
    ctx.bank_account_record(acct)
    refs = ctx.standard_docs(certificates_for=[acct], statements_for=[acct], bank_kwargs={acct.number: {"duplicate_first_interest": True}})
    dup_ref = refs[f"bank:{acct.number}"]["sbint:dup"]
    orig_ref = refs[f"bank:{acct.number}"]["sbint:0"]
    dup_amount = acct.savings_interest[0][1]
    ctx.story += [f"Interest certificate: {amt(acct.savings_total)} (correct)",
                  f"Bank statement lists the Q1 interest credit twice (Transaction #{orig_ref} and #{dup_ref})"]
    ctx.add_hidden(kind="DUPLICATE", category="INTEREST", severity="LOW", title="Duplicate interest credit in the bank statement",
                   description=f"Transaction #{dup_ref} repeats #{orig_ref} ({amt(dup_amount)}); the certificate total does not include it.",
                   correct_answer="Ignore the duplicate row; the certificate figure is correct.", expected_evidence=[f"Transaction #{dup_ref}"],
                   match={"kind": "DUPLICATE", "category": "INTEREST", "key": normalize_key(acct.bank)}, amount=float(dup_amount), fix=imp.add_interest(acct.bank, dup_amount, "SAVINGS"),
                   impact_mode="TAX_DELTA")
    ctx.questions += [{"question": "Why is there a mismatch?", "must_mention": ["twice"], "answer_key": "duplicate row"},
                      {"question": "What information is missing?", "must_mention": [], "answer_key": "nothing material"}]
    return ctx.result("duplicate_transaction", "Duplicate transaction", "A bank statement repeats an interest credit; the certificate is right.",
                      ctx.profile(["SALARY", "INTEREST"]))


def build_missing_income(ctx: Ctx) -> GeneratedCase:
    w = ctx.world_with()
    emp = ctx.b.employer(1840000, idx=0)
    ctx.finalize_employer(emp)
    acct = ctx.b.bank(0, primary=True, savings_annual=18420)
    w.banks.append(acct)
    ctx.bank_account_record(acct)
    refs = ctx.standard_docs(statements_for=[acct])
    sb_ref = refs[f"bank:{acct.number}"]["sbint:0"]
    ais_id = refs["ais_ids"][f"sbint:{acct.number}"]
    ctx.story += [f"Salary {amt(emp.gross_annual)} → Form 16 → 26AS → AIS (all consistent)",
                  f"Bank statement shows {amt(acct.savings_total)} of savings interest (first credit Transaction #{sb_ref}); AIS {ais_id} agrees; not declared"]
    ctx.add_hidden(kind="MISSING", category="INTEREST", severity="MEDIUM", title="Savings-account interest not accounted for",
                   description=f"Bank statement shows {amt(acct.savings_total)} interest income that has not yet been accounted for (AIS {ais_id}).",
                   correct_answer=f"Add savings interest of {amt(acct.savings_total)} from {acct.bank}.", expected_evidence=[f"Transaction #{sb_ref}", f"AIS item {ais_id}"],
                   match={"kind": "MISSING", "category": "INTEREST", "key": normalize_key(acct.bank), "sub_kind": "SAVINGS"}, amount=float(acct.savings_total),
                   fix=imp.add_interest(acct.bank, acct.savings_total, "SAVINGS"))
    ctx.questions += [{"question": "What information is missing?", "must_mention": [amt(acct.savings_total)], "answer_key": "18,420 interest"},
                      {"question": "Show me everything contributing to my taxable income.", "must_mention": [amt(emp.gross_annual)], "answer_key": "salary only"}]
    return ctx.result("missing_income", "Missing income", "The classic case: interest visible in the bank statement and AIS is absent from the return.",
                      ctx.profile(["SALARY"]))


def build_mixed_income(ctx: Ctx) -> GeneratedCase:
    w = ctx.world_with(age=41)
    emp = ctx.b.employer(2600000, idx=1, regime="OLD", nps=True)
    emp.hra_exempt = money(min(emp.hra_annual, Decimal(25000) * 12 - emp.basic_annual * Decimal("0.1"), emp.basic_annual * (Decimal("0.5") if w.person.city_type == "METRO" else Decimal("0.4"))))
    ctx.finalize_employer(emp)
    a = ctx.b.bank(0, primary=True, savings_annual=21300, fds=[(1500000, "0.075")])
    b = ctx.b.bank(3, savings_annual=8240)
    w.banks += [a, b]
    for acct in (a, b):
        ctx.bank_account_record(acct)
    w.trades = ctx.b.trades(n_equity=3, long_term=2)
    w.dividends = ctx.b.dividends(n=3)
    prop = ctx.b.property(monthly_rent=28000, with_tds=False)
    w.properties.append(prop)
    loan = ctx.b.home_loan(interest=245000, principal=180000)
    loan.self_occupied_address = w.person.address
    w.loans.append(loan)
    w.investments = ctx.b.investments(elss=60000, ppf=50000, lic=24000, health=28000, nps_1b=50000)
    w.prev_itr = prev_itr_for(w, Decimal(2380000), {"Salary": Decimal(2150000), "House property": Decimal(210000), "Other sources": Decimal(96000), "Capital gains": Decimal(12000)}, regime="OLD")
    short = Decimal(8000)
    deposited = emp.tds_total - short
    next_fy_amt = Decimal("25312.50")
    refs = ctx.standard_docs(certificates_for=[a], statements_for=[a, b], broker=True, dividend_statement=True, loan_cert=True, prev_itr=True, investment_proof=True,
                             tds_overrides={emp.tan: deposited}, rent_receipts=(Decimal(25000), "Suresh Pillai", "AAKPP1234Q"),
                             bank_kwargs={a.number: {"next_fy_fd_interest": next_fy_amt}, b.number: {"salary_from": [], "rent_from": None, "dividends": []}})
    annual_rent = money(prop.monthly_rent * prop.months)
    ctx.user_entries.append(lambda case, p=prop, r=annual_rent: case.income.rental.append(RentalIncome(property_name=p.name, address=p.address, tenant_name=p.tenant,
                                                                                                        annual_rent_received=TracedValue.of(r, SourceType.USER_INPUT, reference="Entered during onboarding"),
                                                                                                        municipal_taxes_paid=TracedValue.of(p.municipal_tax, SourceType.USER_INPUT), status=ValueStatus.USER_ENTERED)))
    ctx.user_entries.append(lambda case: case.deductions.append(Deduction(section="80D", description="Health insurance – self & family", amount=TracedValue.of(28000, SourceType.USER_INPUT), status=ValueStatus.USER_ENTERED)))
    nfy_ref = refs[f"bank:{a.number}"]["fdint:nextfy"]
    ctx.story += ["Salary (old regime with HRA) → Form 16 (TDS certified) → 26AS shows ₹8,000 less deposited",
                  f"Bank A: certificate + statement; the statement includes an FD interest credit dated {(w.rules.fy_end + timedelta(days=1)).isoformat()} (next FY) – Transaction #{nfy_ref}",
                  f"Bank B: statement only – savings interest {amt(b.savings_total)} undeclared (AIS {refs['ais_ids'][f'sbint:{b.number}']})",
                  "Broker trades, dividends, let-out flat (entered), home loan on self-occupied house, 80C/80D proofs, previous ITR"]
    ctx.add_hidden(kind="MISMATCH", category="TDS", severity="HIGH", title=f"TDS reported by {emp.name} differs from Form 26AS", description=f"Form 16 shows {amt(emp.tds_total)}; 26AS shows {amt(deposited)}.",
                   correct_answer=f"Credit limited to 26AS until the employer files a correction ({amt(short)} at stake).", expected_evidence=["Form 16", "Form 26AS"],
                   match={"kind": "MISMATCH", "category": "TDS", "key": normalize_key(emp.name), "section": "192"}, amount=float(short), fix=None, impact_mode="CREDIT_DIFFERENCE", expected_impact=float(short))
    ctx.add_hidden(kind="MISSING", category="INTEREST", severity="MEDIUM", title=f"Savings interest from {b.bank} not accounted for", description=f"Bank statement and AIS show {amt(b.savings_total)}.",
                   correct_answer=f"Add savings interest of {amt(b.savings_total)} from {b.bank}.", expected_evidence=["Transaction #", f"AIS item {refs['ais_ids'][f'sbint:{b.number}']}"],
                   match={"kind": "MISSING", "category": "INTEREST", "key": normalize_key(b.bank), "sub_kind": "SAVINGS"}, amount=float(b.savings_total), fix=imp.add_interest(b.bank, b.savings_total, "SAVINGS"))
    ctx.add_hidden(kind="TIMING", category="INTEREST", severity="LOW", title="Interest credit dated after the financial year", description=f"Transaction #{nfy_ref} ({amt(next_fy_amt)}) is dated 1 April – it belongs to the next financial year.",
                   correct_answer="Exclude it from this year's return; it will appear in next year's certificate.", expected_evidence=[f"Transaction #{nfy_ref}"],
                   match={"kind": "TIMING", "category": "INTEREST", "key": normalize_key(a.bank)}, amount=float(next_fy_amt), fix=imp.add_interest(a.bank, next_fy_amt, "FIXED_DEPOSIT"))
    ctx.add_hidden(kind="MISMATCH", category="PREVIOUS_YEAR", severity="INFO", title="Previous-year information differs from the current-year profile",
                   description="Last year's return reported house-property and capital-gains income; current-year figures differ materially.", correct_answer="Informational – confirm that all of last year's income sources still apply.",
                   expected_evidence=["ITR"], match={"kind": "MISMATCH", "category": "PREVIOUS_YEAR", "key": ""}, amount=0.0, fix=None, impact_mode="NONE")
    ctx.questions += [{"question": "Why is my tax higher this year?", "must_mention": [], "answer_key": "comparison with previous ITR"},
                      {"question": "What information is missing?", "must_mention": [b.bank.split()[0]], "answer_key": "bank B interest"},
                      {"question": "Explain my capital gains.", "must_mention": [w.trades[0].symbol], "answer_key": "per-trade gains"}]
    return ctx.result("mixed_income", "Mixed income sources", "Everything at once: salary, interest, dividends, capital gains, rent, home loan, investments, previous ITR.",
                      ctx.profile(["SALARY", "INTEREST", "DIVIDEND", "CAPITAL_GAINS", "RENTAL"], regime_preference="OLD"))


SCENARIOS: dict[str, Callable[[Ctx], GeneratedCase]] = {
    "salaried_basic": build_salaried_basic,
    "multiple_employers": build_multiple_employers,
    "salaried_interest": build_salaried_interest,
    "salaried_capital_gains": build_salaried_capital_gains,
    "freelancer": build_freelancer,
    "rental_income": build_rental_income,
    "multiple_bank_accounts": build_multiple_bank_accounts,
    "tds_mismatch": build_tds_mismatch,
    "ais_discrepancy": build_ais_discrepancy,
    "duplicate_transaction": build_duplicate_transaction,
    "missing_income": build_missing_income,
    "mixed_income": build_mixed_income,
}

SCENARIO_META = {
    "salaried_basic": ("Salaried employee (clean)", "Single employer, one savings account, all sources consistent."),
    "multiple_employers": ("Multiple employers", "Job change mid-year; only the first employer's Form 16 is uploaded."),
    "salaried_interest": ("Salaried + interest", "Interest visible in bank statement, 26AS and AIS but not declared."),
    "salaried_capital_gains": ("Salaried + capital gains", "Equity trades and dividends; AIS vs broker mismatch and a fund classification question."),
    "freelancer": ("Freelancer (44ADA)", "Consultant with three clients; one client's fees were not entered."),
    "rental_income": ("Rental income", "Let-out flat with tenant TDS that is not declared."),
    "multiple_bank_accounts": ("Multiple bank accounts", "Three banks: one duplicate entry and one undeclared account."),
    "tds_mismatch": ("TDS mismatch", "Form 16 TDS exceeds what 26AS shows as deposited."),
    "ais_discrepancy": ("AIS discrepancy", "AIS reports higher dividend and FD interest than the taxpayer's statements."),
    "duplicate_transaction": ("Duplicate transaction", "A bank statement repeats an interest credit."),
    "missing_income": ("Missing income", "Interest in the bank statement and AIS is absent from the return."),
    "mixed_income": ("Mixed income sources", "Salary, interest, dividends, capital gains, rent, home loan, investments, previous ITR."),
}
