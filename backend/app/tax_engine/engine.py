"""Deterministic Indian income-tax engine.

The engine never guesses: every number it produces is a ``Line`` with a formula, a rule reference and
the provenance of its inputs. The AI layer may *explain* these lines but never computes tax itself.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Iterable

from app.models.common import (
    SOURCE_LABELS,
    AgeCategory,
    EvidenceRef,
    SourceType,
    TracedValue,
    ValueStatus,
    money,
    round_to_ten,
    rupees,
)
from app.models.tax_model import (
    CapitalGainTransaction,
    Deduction,
    RentalIncome,
    TaxCase,
)
from app.tax_engine.context import TaxContext, build_context
from app.tax_engine.rules import get_rules
from app.tax_engine.rules.base import AYRules, RegimeRules
from app.tax_engine.schema import (
    ComparisonRow,
    Line,
    RegimeComparison,
    RegimeComputation,
    RegimeSummary,
    TaxComputation,
)

ZERO = Decimal("0")


def fmt(v: Decimal | int | float) -> str:
    """Indian grouping: 1234567 -> ₹12,34,567."""
    d = rupees(v)
    neg = d < 0
    s = str(abs(int(d)))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-₹" if neg else "₹") + s


def pct(rate: Decimal) -> str:
    r = (Decimal(rate) * 100).normalize()
    return f"{r:f}%"


def evidence_from(tv: TracedValue, label: str, entity_id: str | None = None) -> list[EvidenceRef]:
    refs = []
    for p in tv.provenance:
        refs.append(
            EvidenceRef(
                label=f"{SOURCE_LABELS.get(p.source_type, p.source_type.value)}"
                + (f" · {p.reference}" if p.reference else ""),
                source_type=p.source_type,
                document_id=p.document_id,
                entity_id=entity_id,
                reference=p.reference,
                amount=tv.amount,
            )
        )
    if not refs:
        refs.append(EvidenceRef(label=label, source_type=SourceType.USER_INPUT, entity_id=entity_id,
                                amount=tv.amount))
    return refs


def months_between(start: date, end: date) -> int:
    """Whole months between two dates (for holding periods)."""
    m = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        m -= 1
    return m


def months_ceil(start: date, end: date) -> int:
    """Months or part thereof from start (inclusive) to end – used for 234A/234B interest."""
    if end < start:
        return 0
    m = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day >= start.day:
        m += 1
    return max(m, 0)


def fy_label_for(d: date) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{str(y + 1)[-2:]}"


def normalize_key(name: str | None) -> str:
    if not name:
        return ""
    s = re.sub(r"[^A-Z0-9 ]", "", name.upper())
    for suffix in (" PRIVATE LIMITED", " PVT LTD", " PVT LIMITED", " LIMITED", " LTD", " LLP", " INC", " CO"):
        s = s.replace(suffix, "")
    return re.sub(r"\s+", "", s)


def rule_119a(v: Decimal) -> Decimal:
    """Rule 119A – ignore any fraction of ₹100 when computing interest."""
    return (Decimal(int(v) // 100) * 100) if v > 0 else ZERO


# ============================================================================= engine


class RegimeEngine:
    def __init__(self, case: TaxCase, rules: AYRules, regime: RegimeRules, ctx: TaxContext):
        self.case = case
        self.rules = rules
        self.regime = regime
        self.ctx = ctx
        self.lines: list[Line] = []
        self.warnings: list[str] = []
        self.assumptions: list[str] = []
        self.credit_ledger: list[dict] = []
        self.summary = RegimeSummary(regime=regime.code)
        self.special_buckets: dict[str, dict] = {}  # key -> {amount, rate, label, bucket, section}
        self.dividend_total = ZERO

    # ------------------------------------------------------------------ helpers
    def ref(self, section: str) -> str:
        return f"{section} · {self.rules.version}"

    # ------------------------------------------------------------------ salary
    def compute_salary(self) -> Line:
        root = Line(id="salary", label="Income from salary", kind="subtotal", rule_ref=self.ref("Sections 15-17"))
        total_gross = ZERO
        total_net = ZERO
        total_pt = ZERO
        for s in self.case.income.salary:
            emp = root.add(Line(id=f"salary.{s.id}", label=f"Salary – {s.employer_name}", kind="subtotal",
                                entity_id=s.id))
            gross = money(s.gross_salary.amount)
            emp.add(Line(id=f"salary.{s.id}.gross", label="Gross salary u/s 17(1)-(3)", amount=gross, kind="input",
                         status=s.gross_salary.status, sources=evidence_from(s.gross_salary, "Gross salary", s.id),
                         rule_ref=self.ref("Section 17")))
            exempt = ZERO
            if self.regime.hra_lta_exemption_allowed:
                exempt = money(s.exempt_allowances.amount)
                hra_note = None
                if s.hra_received and s.basic_salary and s.rent_paid and s.rent_paid.amount > 0:
                    basic = s.basic_salary.amount
                    share = Decimal("0.5") if self.case.taxpayer.city_type == "METRO" else Decimal("0.4")
                    hra_exempt = max(ZERO, min(s.hra_received.amount, s.rent_paid.amount - basic * Decimal("0.1"),
                                               basic * share))
                    hra_exempt = money(hra_exempt)
                    if hra_exempt > exempt:
                        hra_note = (f"HRA exemption computed from rent paid: min(HRA {fmt(s.hra_received.amount)}, "
                                    f"rent − 10% basic {fmt(s.rent_paid.amount - basic * Decimal('0.1'))}, "
                                    f"{pct(share)} of basic {fmt(basic * share)}) = {fmt(hra_exempt)}")
                        exempt = hra_exempt
                ex_line = emp.add(Line(id=f"salary.{s.id}.exempt", label="Less: allowances exempt u/s 10",
                                       amount=-exempt, kind="deduction", status=s.exempt_allowances.status,
                                       sources=evidence_from(s.exempt_allowances, "Exempt allowances", s.id),
                                       rule_ref=self.ref("Section 10(13A)/10(5)")))
                if hra_note:
                    ex_line.notes.append(hra_note)
            else:
                if s.exempt_allowances.amount > 0:
                    emp.add(Line(id=f"salary.{s.id}.exempt", label="Allowances exempt u/s 10 (not available)",
                                 amount=ZERO, kind="info",
                                 notes=[f"{fmt(s.exempt_allowances.amount)} reported as exempt by the employer is "
                                        "not deductible under the new regime (HRA / LTA)."],
                                 rule_ref=self.ref("Section 115BAC(2)")))
                    self.assumptions.append(
                        f"New regime: exempt allowances of {fmt(s.exempt_allowances.amount)} from "
                        f"{s.employer_name} treated as taxable (HRA/LTA not available). If part of it is an "
                        "allowance still exempt under 115BAC, confirm it in the profile.")
            net = gross - exempt
            emp.amount = net
            emp.formula = f"{fmt(gross)} − {fmt(exempt)}"
            total_gross += gross
            total_net += net
            total_pt += money(s.professional_tax.amount)
        if not self.case.income.salary:
            root.amount = ZERO
            return root
        root.add(Line(id="salary.total_17", label="Salary as per section 17 (all employers)", amount=total_net,
                      kind="subtotal", formula="sum of employer lines"))
        std = min(self.regime.standard_deduction_salary, total_net) if total_net > 0 else ZERO
        root.add(Line(id="salary.standard_deduction", label="Less: standard deduction u/s 16(ia)", amount=-std,
                      kind="deduction", formula=f"min({fmt(self.regime.standard_deduction_salary)}, salary)",
                      rule_ref=self.ref("Section 16(ia)"), status=ValueStatus.CALCULATED))
        pt = ZERO
        if self.regime.professional_tax_deductible and total_pt > 0:
            pt = min(total_pt, total_net - std)
            root.add(Line(id="salary.professional_tax", label="Less: professional tax u/s 16(iii)", amount=-pt,
                          kind="deduction", rule_ref=self.ref("Section 16(iii)"), status=ValueStatus.EXTRACTED))
        elif total_pt > 0:
            root.add(Line(id="salary.professional_tax", label="Professional tax (not deductible in new regime)",
                          amount=ZERO, kind="info", notes=[f"{fmt(total_pt)} paid; no deduction under 115BAC."]))
        income = max(ZERO, total_net - std - pt)
        root.amount = income
        root.formula = f"{fmt(total_net)} − {fmt(std)} − {fmt(pt)}"
        self.summary.gross_salary = total_gross
        self.summary.income_salary = income
        return root

    # ------------------------------------------------------------------ house property
    def _effective_properties(self) -> list[RentalIncome]:
        props = list(self.case.income.rental)
        linked_ids = {p.id for p in props}
        for loan in self.case.loans:
            if loan.kind != "HOME":
                continue
            if loan.property_id and loan.property_id in linked_ids:
                continue
            if not props or all(not p.is_self_occupied for p in props):
                # a home loan with no linked property is treated as a self-occupied house
                props.append(RentalIncome(id=f"hp_auto_{loan.id}", property_name=f"Self-occupied home ({loan.lender} loan)",
                                          is_self_occupied=True, interest_on_borrowed_capital=loan.interest_paid,
                                          status=loan.status, document_ids=loan.document_ids))
                self.assumptions.append(
                    f"Home loan from {loan.lender} is not linked to a property; treated as a self-occupied house.")
        return props

    def compute_house_property(self) -> Line:
        root = Line(id="hp", label="Income from house property", kind="subtotal", rule_ref=self.ref("Sections 22-27"))
        total = ZERO
        for p in self._effective_properties():
            pl = root.add(Line(id=f"hp.{p.id}", label=f"{'Self-occupied' if p.is_self_occupied else 'Let-out'} – {p.property_name}",
                               kind="subtotal", entity_id=p.id))
            interest = money(p.interest_on_borrowed_capital.amount)
            loan_int = sum((money(l.interest_paid.amount) for l in self.case.loans if l.kind == "HOME" and l.property_id == p.id), ZERO)
            interest_src = p.interest_on_borrowed_capital
            if loan_int > 0 and interest == ZERO:
                interest = loan_int
                for l in self.case.loans:
                    if l.kind == "HOME" and l.property_id == p.id:
                        interest_src = l.interest_paid
            share = Decimal(p.ownership_share or 1)
            if p.is_self_occupied:
                pl.add(Line(id=f"hp.{p.id}.nav", label="Net annual value", amount=ZERO, kind="input",
                            rule_ref=self.ref("Section 23(2)")))
                allowed = min(interest, self.regime.self_occupied_interest_limit) * share
                if interest > 0:
                    pl.add(Line(id=f"hp.{p.id}.interest", label="Less: interest on borrowed capital u/s 24(b)",
                                amount=-money(allowed), kind="deduction",
                                formula=f"min({fmt(interest)}, limit {fmt(self.regime.self_occupied_interest_limit)}) × share {share}",
                                sources=evidence_from(interest_src, "Home-loan interest", p.id), status=interest_src.status,
                                rule_ref=self.ref("Section 24(b)")))
                    if self.regime.self_occupied_interest_limit == ZERO:
                        pl.children[-1].notes.append("Interest on a self-occupied property is not deductible under the new regime.")
                pl.amount = -money(allowed)
                pl.formula = f"0 − {fmt(allowed)}"
            else:
                rent = money(p.annual_rent_received.amount) * share
                mtax = money(p.municipal_taxes_paid.amount) * share
                gav = pl.add(Line(id=f"hp.{p.id}.gav", label="Gross annual value (rent received)", amount=money(rent),
                                  kind="input", sources=evidence_from(p.annual_rent_received, "Rent received", p.id),
                                  status=p.annual_rent_received.status, rule_ref=self.ref("Section 23(1)")))
                if share != 1:
                    gav.notes.append(f"Ownership share {share} applied.")
                pl.add(Line(id=f"hp.{p.id}.mtax", label="Less: municipal taxes paid", amount=-money(mtax), kind="deduction",
                            sources=evidence_from(p.municipal_taxes_paid, "Municipal taxes", p.id)))
                nav = rent - mtax
                pl.add(Line(id=f"hp.{p.id}.nav", label="Net annual value", amount=money(nav), kind="subtotal",
                            formula=f"{fmt(rent)} − {fmt(mtax)}"))
                std = money(nav * Decimal("0.30")) if nav > 0 else ZERO
                pl.add(Line(id=f"hp.{p.id}.std", label="Less: standard deduction 30% u/s 24(a)", amount=-std,
                            kind="deduction", formula=f"30% × {fmt(nav)}", rule_ref=self.ref("Section 24(a)"),
                            status=ValueStatus.CALCULATED))
                int_allowed = money(interest * share)
                if int_allowed > 0:
                    pl.add(Line(id=f"hp.{p.id}.interest", label="Less: interest on borrowed capital u/s 24(b)",
                                amount=-int_allowed, kind="deduction", rule_ref=self.ref("Section 24(b)"),
                                sources=evidence_from(interest_src, "Loan interest", p.id), status=interest_src.status))
                pl.amount = money(nav - std - int_allowed)
                pl.formula = f"{fmt(nav)} − {fmt(std)} − {fmt(int_allowed)}"
            total += pl.amount
        if total < 0:
            limit = self.regime.hp_loss_setoff_limit
            setoff = max(total, -limit)
            carried = total - setoff
            root.add(Line(id="hp.loss_setoff", label="Loss set off against other heads u/s 71(3A)", amount=money(setoff),
                          kind="adjustment", formula=f"max(loss {fmt(total)}, −{fmt(limit)})",
                          rule_ref=self.ref("Section 71(3A)"), status=ValueStatus.CALCULATED,
                          notes=([f"{fmt(-carried)} of house-property loss cannot be set off this year (carry forward u/s 71B, subject to conditions)."] if carried < 0 else [])))
            if self.regime.hp_loss_setoff_limit == ZERO:
                root.children[-1].notes.append("Under the new regime, house-property loss cannot be set off against other heads.")
            total = setoff
        root.amount = money(total)
        self.summary.income_house_property = root.amount
        return root

    # ------------------------------------------------------------------ capital gains
    def _classify(self, t: CapitalGainTransaction) -> tuple[str, str, Decimal | None, bool, list[str]]:
        """Returns (bucket, rate_label, rate, indexation, notes)."""
        notes: list[str] = []
        transfer = t.transfer_date
        if t.holding_override:
            term = t.holding_override
            notes.append(f"Holding period set by user: {term.lower()}-term.")
        elif t.acquisition_date is None:
            term = "SHORT"
            notes.append("Acquisition date unknown – treated as short-term until confirmed.")
            self.warnings.append(f"Capital-gains item '{t.description}' has no acquisition date; treated as short-term.")
        else:
            held = months_between(t.acquisition_date, transfer)
            threshold = self.rules.holding_months(t.asset_class, transfer)
            term = "LONG" if held > threshold else "SHORT"
            notes.append(f"Held {held} months (threshold {threshold} months) → {term.lower()}-term.")
        if t.asset_class == "DEBT_MF" and t.acquisition_date and t.acquisition_date >= self.rules.specified_mf_cutoff:
            term = "SHORT"
            notes.append("Specified mutual fund acquired on/after 1 Apr 2023 – deemed short-term u/s 50AA.")
        equity = t.asset_class in ("LISTED_EQUITY", "EQUITY_MF") and t.stt_paid
        if term == "SHORT":
            bucket = "STCG_111A" if equity else "STCG_OTHER"
        else:
            bucket = "LTCG_112A" if equity else "LTCG_112"
        rr = self.rules.cg_rate(bucket, transfer)
        return bucket, rr.label, rr.rate, rr.indexation, notes

    def _indexed_cost(self, t: CapitalGainTransaction) -> Decimal | None:
        if not t.acquisition_date:
            return None
        c_acq = self.rules.cii.get(fy_label_for(t.acquisition_date))
        c_sale = self.rules.cii.get(fy_label_for(t.transfer_date))
        if not c_acq or not c_sale:
            return None
        return money((t.cost_of_acquisition.amount + t.improvement_cost.amount) * Decimal(c_sale) / Decimal(c_acq))

    def compute_capital_gains(self) -> Line:
        root = Line(id="cg", label="Capital gains", kind="subtotal", rule_ref=self.ref("Sections 45-55A"))
        buckets: dict[str, dict] = {}
        for t in self.case.income.capital_gains:
            bucket, label, rate, indexation, notes = self._classify(t)
            sale = money(t.sale_consideration.amount)
            cost = money(t.cost_of_acquisition.amount + t.improvement_cost.amount)
            exp = money(t.transfer_expenses.amount)
            gain = sale - exp - cost
            tl = root.add(Line(id=f"cg.{t.id}", label=f"{t.description} ({t.asset_class.replace('_', ' ').title()})",
                               kind="subtotal", entity_id=t.id, notes=notes, rule_ref=self.ref(label)))
            tl.add(Line(id=f"cg.{t.id}.sale", label="Full value of consideration", amount=sale, kind="input",
                        sources=evidence_from(t.sale_consideration, "Sale consideration", t.id), status=t.sale_consideration.status))
            tl.add(Line(id=f"cg.{t.id}.exp", label="Less: transfer expenses", amount=-exp, kind="deduction",
                        sources=evidence_from(t.transfer_expenses, "Expenses", t.id)))
            if bucket == "LTCG_112" and indexation:
                idx = self._indexed_cost(t)
                if idx is not None:
                    tl.add(Line(id=f"cg.{t.id}.cost", label="Less: indexed cost of acquisition", amount=-idx, kind="deduction",
                                formula=f"{fmt(cost)} × CII({fy_label_for(t.transfer_date)}) / CII({fy_label_for(t.acquisition_date)})",
                                sources=evidence_from(t.cost_of_acquisition, "Cost", t.id)))
                    gain = sale - exp - idx
            elif bucket == "LTCG_112" and t.asset_class == "IMMOVABLE_PROPERTY" and t.acquisition_date and t.acquisition_date < date(2024, 7, 23):
                idx = self._indexed_cost(t)
                alt = self.rules.cg_rate("LTCG_112_INDEXED", t.transfer_date)
                if idx is not None and rate is not None:
                    tax_plain = max(ZERO, sale - exp - cost) * rate
                    tax_idx = max(ZERO, sale - exp - idx) * alt.rate
                    if tax_idx < tax_plain:
                        tl.add(Line(id=f"cg.{t.id}.cost", label="Less: indexed cost of acquisition (option exercised)", amount=-idx,
                                    kind="deduction", formula=f"{fmt(cost)} × CII({fy_label_for(t.transfer_date)}) / CII({fy_label_for(t.acquisition_date)})",
                                    sources=evidence_from(t.cost_of_acquisition, "Cost", t.id)))
                        gain = sale - exp - idx
                        rate, label = alt.rate, alt.label
                        tl.notes.append("Lower of 12.5% without indexation and 20% with indexation applied (property acquired before 23 Jul 2024).")
                    else:
                        tl.add(Line(id=f"cg.{t.id}.cost", label="Less: cost of acquisition", amount=-cost, kind="deduction",
                                    sources=evidence_from(t.cost_of_acquisition, "Cost", t.id)))
                        tl.notes.append("12.5% without indexation is lower than 20% with indexation for this asset.")
                else:
                    tl.add(Line(id=f"cg.{t.id}.cost", label="Less: cost of acquisition", amount=-cost, kind="deduction",
                                sources=evidence_from(t.cost_of_acquisition, "Cost", t.id)))
            else:
                tl.add(Line(id=f"cg.{t.id}.cost", label="Less: cost of acquisition", amount=-cost, kind="deduction",
                            sources=evidence_from(t.cost_of_acquisition, "Cost", t.id), status=t.cost_of_acquisition.status))
            gain = money(gain)
            tl.amount = gain
            tl.formula = f"{fmt(sale)} − {fmt(exp)} − cost"
            key = f"{bucket}@{rate if rate is not None else 'slab'}"
            b = buckets.setdefault(key, {"bucket": bucket, "rate": rate, "label": label, "amount": ZERO, "items": []})
            b["amount"] += gain
            b["items"].append(t.id)
        if not buckets:
            return root
        # ---- intra-head set-off
        short_keys = [k for k in buckets if buckets[k]["bucket"].startswith("STCG")]
        long_keys = [k for k in buckets if buckets[k]["bucket"].startswith("LTCG")]
        def order(keys):  # highest rate first (slab treated as 30% for ordering)
            return sorted(keys, key=lambda k: -(buckets[k]["rate"] if buckets[k]["rate"] is not None else Decimal("0.30")))
        loss_short = sum((-buckets[k]["amount"] for k in short_keys if buckets[k]["amount"] < 0), ZERO)
        loss_long = sum((-buckets[k]["amount"] for k in long_keys if buckets[k]["amount"] < 0), ZERO)
        for k in short_keys + long_keys:
            if buckets[k]["amount"] < 0:
                buckets[k]["amount"] = ZERO
        setoff_lines = []
        for pool_name, pool, targets in (("short-term loss", loss_short, order(short_keys) + order(long_keys)),
                                         ("long-term loss", loss_long, order(long_keys))):
            remaining = pool
            for k in targets:
                if remaining <= 0:
                    break
                take = min(remaining, buckets[k]["amount"])
                if take > 0:
                    buckets[k]["amount"] -= take
                    remaining -= take
                    setoff_lines.append(f"{pool_name} of {fmt(take)} set off against {buckets[k]['label']}")
            if remaining > 0:
                self.warnings.append(f"Unabsorbed {pool_name} of {fmt(remaining)} may be carried forward (section 74) if the return is filed on time.")
        if setoff_lines:
            root.add(Line(id="cg.setoff", label="Intra-head set-off of losses", amount=ZERO, kind="adjustment",
                          notes=setoff_lines, rule_ref=self.ref("Section 70")))
        total = ZERO
        for k, b in buckets.items():
            amt = money(b["amount"])
            total += amt
            self.special_buckets[k] = {**b, "amount": amt}
            root.add(Line(id=f"cg.bucket.{k}", label=f"Net {b['label']}", amount=amt, kind="subtotal",
                          formula="sum of items after set-off", rule_ref=self.ref(b["label"]), status=ValueStatus.CALCULATED))
        root.amount = money(total)
        root.formula = "sum of net gains by bucket"
        self.summary.income_capital_gains = root.amount
        return root

    # ------------------------------------------------------------------ business / profession
    def compute_business(self) -> Line:
        root = Line(id="business", label="Profits and gains of business or profession", kind="subtotal",
                    rule_ref=self.ref("Sections 28-44ADA"))
        total = ZERO
        for b in self.case.income.business:
            bl = root.add(Line(id=f"business.{b.id}", label=b.description, kind="subtotal", entity_id=b.id))
            receipts = money(b.gross_receipts.amount)
            bl.add(Line(id=f"business.{b.id}.receipts", label="Gross receipts", amount=receipts, kind="input",
                        sources=evidence_from(b.gross_receipts, "Gross receipts", b.id), status=b.gross_receipts.status))
            digital = Decimal(b.digital_receipts_share or 1)
            if b.nature == "PROFESSION_44ADA":
                limit = self.rules.presumptive_44ada_limit_digital if digital >= Decimal("0.95") else self.rules.presumptive_44ada_limit
                if receipts > limit:
                    self.warnings.append(f"Gross receipts of {fmt(receipts)} exceed the 44ADA limit of {fmt(limit)}; presumptive taxation is not available – regular books required.")
                    profit = money(receipts - b.expenses.amount)
                    bl.add(Line(id=f"business.{b.id}.profit", label="Net profit (regular computation)", amount=profit, kind="subtotal"))
                else:
                    presumptive = money(receipts * self.rules.presumptive_44ada_rate)
                    declared = money(b.declared_profit.amount) if b.declared_profit else ZERO
                    profit = max(presumptive, declared)
                    bl.add(Line(id=f"business.{b.id}.profit", label="Presumptive income u/s 44ADA", amount=profit, kind="subtotal",
                                formula=f"{pct(self.rules.presumptive_44ada_rate)} × {fmt(receipts)}" + (f" (declared {fmt(declared)})" if declared > presumptive else ""),
                                rule_ref=self.ref("Section 44ADA"), status=ValueStatus.CALCULATED))
            elif b.nature == "BUSINESS_44AD":
                limit = self.rules.presumptive_44ad_limit_digital if digital >= Decimal("0.95") else self.rules.presumptive_44ad_limit
                if receipts > limit:
                    self.warnings.append(f"Turnover of {fmt(receipts)} exceeds the 44AD limit of {fmt(limit)}; regular books required.")
                    profit = money(receipts - b.expenses.amount)
                    bl.add(Line(id=f"business.{b.id}.profit", label="Net profit (regular computation)", amount=profit, kind="subtotal"))
                else:
                    presumptive = money(receipts * (digital * self.rules.presumptive_44ad_rate_digital + (1 - digital) * self.rules.presumptive_44ad_rate_cash))
                    declared = money(b.declared_profit.amount) if b.declared_profit else ZERO
                    profit = max(presumptive, declared)
                    bl.add(Line(id=f"business.{b.id}.profit", label="Presumptive income u/s 44AD", amount=profit, kind="subtotal",
                                formula=f"6% digital / 8% other × {fmt(receipts)}", rule_ref=self.ref("Section 44AD"), status=ValueStatus.CALCULATED))
            else:
                exp = money(b.expenses.amount)
                bl.add(Line(id=f"business.{b.id}.expenses", label="Less: expenses", amount=-exp, kind="deduction",
                            sources=evidence_from(b.expenses, "Expenses", b.id)))
                profit = money(b.declared_profit.amount) if b.declared_profit else money(receipts - exp)
                bl.add(Line(id=f"business.{b.id}.profit", label="Net profit", amount=profit, kind="subtotal"))
            bl.amount = profit
            total += profit
        if total < 0:
            self.warnings.append(f"Business loss of {fmt(-total)} can be set off against other heads except salary; carry-forward requires timely filing.")
        root.amount = money(total)
        self.summary.income_business = root.amount
        return root

    # ------------------------------------------------------------------ other sources
    def compute_other_sources(self) -> Line:
        root = Line(id="os", label="Income from other sources", kind="subtotal", rule_ref=self.ref("Sections 56-58"))
        total = ZERO
        if self.case.income.interest:
            il = root.add(Line(id="os.interest", label="Interest income", kind="subtotal"))
            isum = ZERO
            for i in self.case.income.interest:
                amt = money(i.amount.amount)
                il.add(Line(id=f"os.interest.{i.id}", label=f"{i.payer_name} – {i.kind.replace('_', ' ').title()}" + (f" ({i.account_ref})" if i.account_ref else ""),
                            amount=amt, kind="input", entity_id=i.id, sources=evidence_from(i.amount, "Interest", i.id), status=i.amount.status))
                isum += amt
            il.amount = isum
            total += isum
        if self.case.income.dividend:
            dl = root.add(Line(id="os.dividend", label="Dividend income", kind="subtotal"))
            dsum = ZERO
            for d in self.case.income.dividend:
                amt = money(d.amount.amount)
                dl.add(Line(id=f"os.dividend.{d.id}", label=d.payer_name, amount=amt, kind="input", entity_id=d.id,
                            sources=evidence_from(d.amount, "Dividend", d.id), status=d.amount.status))
                dsum += amt
            dl.amount = dsum
            total += dsum
            self.dividend_total = dsum
        for o in self.case.income.other_sources:
            amt = money(o.amount.amount)
            ol = root.add(Line(id=f"os.other.{o.id}", label=o.description, amount=amt, kind="input", entity_id=o.id,
                               sources=evidence_from(o.amount, o.description, o.id), status=o.amount.status))
            if o.category == "FAMILY_PENSION":
                ded = min(money(amt / 3), self.regime.family_pension_deduction_cap)
                ol.add(Line(id=f"os.other.{o.id}.57iia", label="Less: deduction u/s 57(iia)", amount=-ded, kind="deduction",
                            formula=f"min(1/3 × {fmt(amt)}, {fmt(self.regime.family_pension_deduction_cap)})", rule_ref=self.ref("Section 57(iia)")))
                amt -= ded
                ol.amount = amt
            total += amt
        root.amount = money(total)
        self.summary.income_other_sources = root.amount
        return root

    # ------------------------------------------------------------------ deductions
    def _deduction_claims(self) -> list[dict]:
        claims: list[dict] = []
        for d in self.case.deductions:
            claims.append({"section": d.section, "label": d.description, "amount": money(d.amount.amount), "tv": d.amount,
                           "entity_id": d.id, "rate": d.qualifying_rate, "senior_parents": d.for_senior_parents})
        inv_map = {"PPF": "80C", "ELSS": "80C", "LIFE_INSURANCE": "80C", "EPF": "80C", "NSC": "80C", "TAX_SAVER_FD": "80C",
                   "SSY": "80C", "HOME_LOAN_PRINCIPAL": "80C", "TUITION_FEES": "80C", "NPS": "80CCD1", "HEALTH_INSURANCE": "80D"}
        for inv in self.case.investments:
            sec = inv.section_hint or inv_map.get(inv.instrument, "80C")
            claims.append({"section": sec, "label": f"{inv.instrument.replace('_', ' ').title()}" + (f" – {inv.provider}" if inv.provider else ""),
                           "amount": money(inv.amount.amount), "tv": inv.amount, "entity_id": inv.id, "rate": None, "senior_parents": False})
        for loan in self.case.loans:
            if loan.kind == "HOME" and loan.principal_repaid.amount > 0:
                claims.append({"section": "80C", "label": f"Home-loan principal – {loan.lender}", "amount": money(loan.principal_repaid.amount),
                               "tv": loan.principal_repaid, "entity_id": loan.id, "rate": None, "senior_parents": False})
            if loan.kind == "EDUCATION":
                claims.append({"section": "80E", "label": f"Education-loan interest – {loan.lender}", "amount": money(loan.interest_paid.amount),
                               "tv": loan.interest_paid, "entity_id": loan.id, "rate": None, "senior_parents": False})
            if loan.kind == "ELECTRIC_VEHICLE":
                claims.append({"section": "80EEB", "label": f"EV-loan interest – {loan.lender}", "amount": money(loan.interest_paid.amount),
                               "tv": loan.interest_paid, "entity_id": loan.id, "rate": None, "senior_parents": False})
        for s in self.case.income.salary:
            if s.employer_nps_contribution.amount > 0:
                claims.append({"section": "80CCD2", "label": f"Employer NPS contribution – {s.employer_name}", "amount": money(s.employer_nps_contribution.amount),
                               "tv": s.employer_nps_contribution, "entity_id": s.id, "rate": None, "senior_parents": False, "salary": s})
        # entitlement-based interest deductions computed automatically
        if self.ctx.is_senior:
            dep = sum((money(i.amount.amount) for i in self.case.income.interest if i.kind in ("SAVINGS", "FIXED_DEPOSIT", "RECURRING_DEPOSIT")), ZERO)
            if dep > 0:
                claims.append({"section": "80TTB", "label": "Deposit interest (senior citizen) – auto", "amount": dep,
                               "tv": TracedValue.of(dep, SourceType.CALCULATION, note="Sum of bank/post-office deposit interest"), "entity_id": None, "rate": None, "senior_parents": False, "auto": True})
        else:
            sav = sum((money(i.amount.amount) for i in self.case.income.interest if i.kind == "SAVINGS"), ZERO)
            if sav > 0:
                claims.append({"section": "80TTA", "label": "Savings-account interest – auto", "amount": sav,
                               "tv": TracedValue.of(sav, SourceType.CALCULATION, note="Sum of savings-account interest"), "entity_id": None, "rate": None, "senior_parents": False, "auto": True})
        return claims

    def compute_deductions(self, gti: Decimal, special_income: Decimal) -> Line:
        root = Line(id="via", label="Deductions under Chapter VI-A", kind="subtotal", rule_ref=self.ref("Chapter VI-A"))
        claims = self._deduction_claims()
        allowed_sections = set(self.regime.chapter_via_allowed_sections)
        limits = self.rules.deduction_limits
        grouped: dict[str, list[dict]] = defaultdict(list)
        for c in claims:
            grouped[c["section"]].append(c)
        total = ZERO
        group_80c_used = ZERO
        for section in sorted(grouped, key=lambda s: (s[:3], s)):
            items = grouped[section]
            claimed = sum((c["amount"] for c in items), ZERO)
            lim = limits.get(section)
            sec_line = root.add(Line(id=f"via.{section}", label=f"Section {section.replace('CCD1B', 'CCD(1B)').replace('CCD1', 'CCD(1)').replace('CCD2', 'CCD(2)')} – {lim.label if lim else ''}",
                                     kind="deduction", rule_ref=self.ref(f"Section {section}")))
            for c in items:
                sec_line.add(Line(id=f"via.{section}.{c['entity_id'] or 'auto'}", label=c["label"], amount=c["amount"], kind="input",
                                  entity_id=c["entity_id"], sources=evidence_from(c["tv"], c["label"], c["entity_id"]), status=c["tv"].status))
            if section not in allowed_sections:
                sec_line.amount = ZERO
                sec_line.notes.append(f"{fmt(claimed)} claimed – not available under the {self.regime.label.lower()}.")
                sec_line.kind = "info"
                continue
            allowed = claimed
            if section in ("80C", "80CCC", "80CCD1"):
                cap = limits["80C"].limit
                room = max(ZERO, cap - group_80c_used)
                allowed = min(claimed, room)
                group_80c_used += allowed
                sec_line.formula = f"min({fmt(claimed)}, aggregate cap {fmt(cap)} u/s 80CCE)"
            elif section == "80CCD2":
                allowed = ZERO
                for c in items:
                    s = c.get("salary")
                    base = s.basic_salary.amount if (s and s.basic_salary) else (s.gross_salary.amount if s else ZERO)
                    cap = money(base * self.regime.employer_nps_rate)
                    allowed += min(c["amount"], cap)
                    if s and not s.basic_salary:
                        self.assumptions.append(f"80CCD(2) for {s.employer_name}: basic salary not available; cap computed on gross salary.")
                sec_line.formula = f"min(contribution, {pct(self.regime.employer_nps_rate)} of salary)"
            elif section == "80D":
                self_cap = limits["80D"].senior_limit if self.ctx.is_senior else limits["80D"].limit
                self_claim = sum((c["amount"] for c in items if not c["senior_parents"]), ZERO)
                par_claim = sum((c["amount"] for c in items if c["senior_parents"]), ZERO)
                allowed = min(self_claim, self_cap) + min(par_claim, limits["80D"].senior_limit)
                sec_line.formula = f"min(self {fmt(self_claim)}, {fmt(self_cap)}) + min(senior parents {fmt(par_claim)}, {fmt(limits['80D'].senior_limit)})"
            elif section == "80TTA":
                allowed = ZERO if self.ctx.is_senior else min(claimed, limits["80TTA"].limit)
                sec_line.formula = f"min({fmt(claimed)}, {fmt(limits['80TTA'].limit)})"
            elif section == "80TTB":
                allowed = min(claimed, limits["80TTB"].limit) if self.ctx.is_senior else ZERO
                sec_line.formula = f"min({fmt(claimed)}, {fmt(limits['80TTB'].limit)})"
                if not self.ctx.is_senior:
                    sec_line.notes.append("80TTB applies to senior citizens only.")
            elif section == "80G":
                allowed = sum((money(c["amount"] * Decimal(c["rate"] if c["rate"] is not None else "0.5")) for c in items), ZERO)
                sec_line.formula = "donation × qualifying rate (50% assumed where not specified)"
                self.assumptions.append("80G: qualifying-limit (10% of adjusted GTI) donations are not modelled; verify eligibility per receipt.")
            elif lim and lim.limit is not None:
                cap = lim.senior_limit if (self.ctx.is_senior and lim.senior_limit) else lim.limit
                allowed = min(claimed, cap)
                sec_line.formula = f"min({fmt(claimed)}, {fmt(cap)})"
            sec_line.amount = money(allowed)
            if allowed < claimed:
                sec_line.notes.append(f"Claimed {fmt(claimed)}; restricted to {fmt(allowed)}.")
            total += money(allowed)
        cap_total = max(ZERO, gti - special_income)
        if total > cap_total:
            root.add(Line(id="via.cap", label="Restricted to gross total income excluding special-rate income", amount=-(total - cap_total),
                          kind="adjustment", notes=["Chapter VI-A deductions cannot exceed GTI and are not allowed against STCG 111A / LTCG."]))
            total = cap_total
        root.amount = money(total)
        root.formula = "sum of allowed sections"
        self.summary.total_deductions = root.amount
        return root

    # ------------------------------------------------------------------ tax computation
    def slab_tax(self, income: Decimal, slabs) -> tuple[Decimal, list[Line]]:
        tax = ZERO
        lines: list[Line] = []
        for i, s in enumerate(slabs):
            lower, upper = Decimal(s.lower), (Decimal(s.upper) if s.upper is not None else None)
            if income <= lower:
                break
            portion = (min(income, upper) if upper is not None else income) - lower
            t = money(portion * Decimal(s.rate))
            tax += t
            band = f"{fmt(lower)} – {fmt(upper)}" if upper is not None else f"above {fmt(lower)}"
            lines.append(Line(id=f"tax.slab.{i}", label=f"{pct(Decimal(s.rate))} on {band}", amount=t, kind="tax",
                              formula=f"{pct(Decimal(s.rate))} × {fmt(portion)}"))
        return money(tax), lines

    def compute_tax(self, taxable_income: Decimal) -> list[Line]:
        out: list[Line] = []
        age = self.ctx.age_category
        slabs = self.regime.slabs.get(age) or self.regime.slabs[AgeCategory.GENERAL]
        basic_exemption = Decimal(slabs[0].upper or 0)
        # ---- special-rate income after the 112A exemption
        special: list[dict] = []
        exemption_left = self.rules.ltcg_112a_exemption
        for key, b in self.special_buckets.items():
            amt = b["amount"]
            if amt <= 0 or b["rate"] is None:
                continue  # STCG_OTHER is taxed at slab rates as part of normal income
            if b["bucket"] == "LTCG_112A":
                ex = min(amt, exemption_left)
                exemption_left -= ex
                amt = amt - ex
                b["exempt"] = ex
            special.append({**b, "taxable": amt})
        # income taxed at special rates (including the exempt slice of 112A gains) is never taxed at slab rates
        special_gross = sum((s["amount"] for s in special), ZERO)
        normal_income = max(ZERO, taxable_income - special_gross)
        # ---- unexhausted basic exemption against special-rate income (residents only)
        if self.ctx.is_resident and normal_income < basic_exemption:
            shortfall = basic_exemption - normal_income
            for s in sorted(special, key=lambda s: -s["rate"]):
                take = min(shortfall, s["taxable"])
                if take > 0:
                    s["taxable"] -= take
                    s["adjusted"] = take
                    shortfall -= take
            if shortfall < basic_exemption - normal_income:
                self.assumptions.append("Unexhausted basic exemption applied against special-rate gains, highest rate first (resident benefit).")
        normal_root = Line(id="tax.normal", label=f"Tax on income taxed at slab rates ({fmt(normal_income)})", kind="tax",
                           rule_ref=self.ref(self.regime.section), formula="sum of slab lines")
        t_normal, slab_lines = self.slab_tax(normal_income, slabs)
        normal_root.children = slab_lines
        normal_root.amount = t_normal
        if age != AgeCategory.GENERAL and self.regime.code == "OLD":
            normal_root.notes.append(f"{age.value.replace('_', ' ').title()} citizen basic exemption of {fmt(basic_exemption)} applied.")
        out.append(normal_root)
        t_special = ZERO
        if special:
            sp_root = Line(id="tax.special", label="Tax on income taxed at special rates", kind="tax", formula="sum of special-rate lines")
            for s in special:
                t = money(s["taxable"] * s["rate"])
                t_special += t
                l = sp_root.add(Line(id=f"tax.special.{s['bucket']}", label=f"{s['label']}", amount=t, kind="tax",
                                     formula=f"{pct(s['rate'])} × {fmt(s['taxable'])}", rule_ref=self.ref(s["bucket"].replace("_", " "))))
                if s.get("exempt"):
                    l.notes.append(f"Exemption of {fmt(s['exempt'])} u/s 112A applied.")
                if s.get("adjusted"):
                    l.notes.append(f"{fmt(s['adjusted'])} absorbed by the unexhausted basic exemption.")
            sp_root.amount = money(t_special)
            out.append(sp_root)
        tax_before_rebate = t_normal + t_special
        self.summary.tax_on_normal_income = t_normal
        self.summary.tax_on_special_income = money(t_special)
        self.summary.tax_before_rebate = money(tax_before_rebate)
        # ---- rebate 87A
        rebate = ZERO
        rr = self.regime.rebate
        if self.ctx.is_resident:
            base_for_rebate = tax_before_rebate if rr.applies_to_special_rate_income else t_normal
            if taxable_income <= rr.income_threshold:
                rebate = min(base_for_rebate, rr.max_rebate)
                formula = f"min(tax {fmt(base_for_rebate)}, {fmt(rr.max_rebate)}) as income ≤ {fmt(rr.income_threshold)}"
            elif rr.marginal_relief:
                excess = taxable_income - rr.income_threshold
                if base_for_rebate > excess:
                    rebate = base_for_rebate - excess
                    formula = f"marginal relief: tax {fmt(base_for_rebate)} − income above threshold {fmt(excess)}"
                else:
                    formula = "not applicable (income above threshold)"
            else:
                formula = "not applicable (income above threshold)"
        else:
            formula = "not available to non-residents"
        rebate = money(rebate)
        out.append(Line(id="tax.rebate", label="Less: rebate u/s 87A", amount=-rebate, kind="deduction", formula=formula,
                        rule_ref=self.ref("Section 87A"), status=ValueStatus.CALCULATED,
                        notes=(["Rebate is not applied to income taxed at special rates."] if special and rebate > 0 else [])))
        tax_after_rebate = money(tax_before_rebate - rebate)
        self.summary.rebate_87a = rebate
        self.summary.tax_after_rebate = tax_after_rebate
        # ---- surcharge
        surcharge = ZERO
        rate = ZERO
        for band in self.regime.surcharge_bands:
            if taxable_income > band.above:
                rate = Decimal(band.rate)
        if rate > 0 and tax_after_rebate > 0:
            cap = Decimal(self.regime.surcharge_cap_on_special_income)
            special_tax = t_special
            # dividend portion of slab tax attracts the capped surcharge as well
            div_tax = ZERO
            if self.dividend_total > 0 and normal_income > 0:
                div_tax = money(t_normal * min(self.dividend_total, normal_income) / normal_income)
            capped_base = min(tax_after_rebate, special_tax + div_tax)
            other_base = tax_after_rebate - capped_base
            surcharge = money(other_base * rate + capped_base * min(rate, cap))
            # marginal relief: tax + surcharge should not exceed tax at threshold + income above threshold
            threshold = max((b.above for b in self.regime.surcharge_bands if taxable_income > b.above), default=ZERO)
            lower_rate = ZERO
            for band in self.regime.surcharge_bands:
                if threshold > band.above:
                    lower_rate = Decimal(band.rate)
            t_thr, _ = self.slab_tax(threshold, slabs)
            tax_at_threshold = t_thr + t_thr * lower_rate
            max_total = tax_at_threshold + (taxable_income - threshold)
            if tax_after_rebate + surcharge > max_total:
                relief = tax_after_rebate + surcharge - max_total
                surcharge = money(max(ZERO, surcharge - relief))
                self.assumptions.append(f"Marginal relief on surcharge applied ({fmt(relief)}); computed on slab-rate income at the threshold.")
        surcharge = money(surcharge)
        out.append(Line(id="tax.surcharge", label="Surcharge", amount=surcharge, kind="tax",
                        formula=(f"{pct(rate)} of tax (capped at {pct(Decimal(self.regime.surcharge_cap_on_special_income))} on dividends / capital gains)" if rate > 0 else "not applicable below ₹50 lakh"),
                        rule_ref=self.ref("Finance Act – surcharge")))
        cess = money((tax_after_rebate + surcharge) * self.rules.cess_rate)
        out.append(Line(id="tax.cess", label="Health and education cess", amount=cess, kind="tax",
                        formula=f"{pct(self.rules.cess_rate)} × ({fmt(tax_after_rebate)} + {fmt(surcharge)})", rule_ref=self.ref("Section 2(11) Finance Act")))
        total = money(tax_after_rebate + surcharge + cess)
        self.summary.surcharge = surcharge
        self.summary.cess = cess
        self.summary.total_tax_liability = total
        out.append(Line(id="tax.total", label="Total tax liability", amount=total, kind="result",
                        formula=f"{fmt(tax_after_rebate)} + {fmt(surcharge)} + {fmt(cess)}", status=ValueStatus.CALCULATED))
        return out

    # ------------------------------------------------------------------ credits
    def compute_credits(self) -> Line:
        root = Line(id="credits", label="Taxes already paid", kind="credit", rule_ref=self.ref("Sections 199, 206C, 207-211"))
        priority = {ValueStatus.USER_CONFIRMED: 0}
        src_priority = {SourceType.FORM26AS: 1, SourceType.FORM16: 2, SourceType.FORM16A: 2, SourceType.AIS: 3,
                        SourceType.TIS: 4, SourceType.USER_INPUT: 5}
        groups: dict[tuple, list] = defaultdict(list)
        for t in self.case.tax_deducted:
            key = (t.kind, t.deductor_tan or normalize_key(t.deductor_name), t.section.replace("-", "").upper())
            groups[key].append(t)
        tds_total = ZERO
        tcs_total = ZERO
        tds_line = root.add(Line(id="credits.tds", label="Tax deducted at source (TDS)", kind="credit", formula="sum of deductors"))
        tcs_line = root.add(Line(id="credits.tcs", label="Tax collected at source (TCS)", kind="credit"))
        seen_entities: set[str] = set()
        for key, items in groups.items():
            items_sorted = sorted(items, key=lambda t: (priority.get(t.status, 9), src_priority.get(t.source_type, 8)))
            chosen = items_sorted[0]
            amounts = {money(t.tax_deducted.amount) for t in items}
            l = Line(id=f"credits.{chosen.kind.lower()}.{chosen.id}", label=f"{chosen.deductor_name} · section {chosen.section}",
                     amount=money(chosen.tax_deducted.amount), kind="credit", entity_id=chosen.id,
                     sources=[EvidenceRef(label=f"{SOURCE_LABELS[t.source_type]}" + (f" · {t.period}" if t.period else ""), source_type=t.source_type,
                                          document_id=(t.document_ids[0] if t.document_ids else None), entity_id=t.id, amount=t.tax_deducted.amount) for t in items],
                     status=chosen.status)
            if len(amounts) > 1:
                l.notes.append("Sources report different amounts: " + "; ".join(f"{SOURCE_LABELS[t.source_type]} {fmt(t.tax_deducted.amount)}" for t in items_sorted)
                               + f". Using {SOURCE_LABELS[chosen.source_type]} pending reconciliation.")
                l.status = ValueStatus.UNRESOLVED if chosen.status != ValueStatus.USER_CONFIRMED else chosen.status
            self.credit_ledger.append({"kind": chosen.kind, "deductor": chosen.deductor_name, "section": chosen.section,
                                       "used": float(chosen.tax_deducted.amount), "used_source": chosen.source_type.value,
                                       "candidates": [{"source": t.source_type.value, "amount": float(t.tax_deducted.amount), "id": t.id} for t in items_sorted],
                                       "conflict": len(amounts) > 1})
            if chosen.linked_income_id:
                seen_entities.add(chosen.linked_income_id)
            if chosen.kind == "TDS":
                tds_line.add(l)
                tds_total += l.amount
            else:
                tcs_line.add(l)
                tcs_total += l.amount
        # fall back to TDS recorded on income entities that have no ledger entry
        known_keys = {normalize_key(t.deductor_name) for t in self.case.tax_deducted} | {t.deductor_tan for t in self.case.tax_deducted if t.deductor_tan}
        for ent, payer, tds in self._entity_tds():
            if tds.amount <= 0 or ent.id in seen_entities:
                continue
            if normalize_key(payer) in known_keys or getattr(ent, "employer_tan", None) in known_keys or getattr(ent, "payer_tan", None) in known_keys:
                continue
            l = tds_line.add(Line(id=f"credits.tds.entity.{ent.id}", label=f"{payer} (as per income document)", amount=money(tds.amount), kind="credit",
                                  entity_id=ent.id, sources=evidence_from(tds, "TDS", ent.id), status=tds.status,
                                  notes=["No Form 26AS / AIS entry found for this deductor – credit is provisional until it appears in 26AS."]))
            tds_total += l.amount
            self.credit_ledger.append({"kind": "TDS", "deductor": payer, "section": "-", "used": float(tds.amount), "used_source": "INCOME_DOCUMENT",
                                       "candidates": [], "conflict": False, "provisional": True})
        tds_line.amount = money(tds_total)
        tcs_line.amount = money(tcs_total)
        adv = ZERO
        sat = ZERO
        for p in self.case.tax_payments:
            l = root.add(Line(id=f"credits.pay.{p.id}", label=f"{'Advance tax' if p.kind == 'ADVANCE_TAX' else 'Self-assessment tax'} paid on {p.paid_on.isoformat()}" + (f" · challan {p.challan_ref}" if p.challan_ref else ""),
                              amount=money(p.amount.amount), kind="credit", entity_id=p.id, sources=evidence_from(p.amount, "Tax payment", p.id), status=p.status))
            if p.kind == "ADVANCE_TAX":
                adv += l.amount
            else:
                sat += l.amount
        root.amount = money(tds_total + tcs_total + adv + sat)
        root.formula = f"TDS {fmt(tds_total)} + TCS {fmt(tcs_total)} + advance tax {fmt(adv)} + self-assessment {fmt(sat)}"
        self.summary.tds, self.summary.tcs, self.summary.advance_tax, self.summary.self_assessment_tax = money(tds_total), money(tcs_total), money(adv), money(sat)
        self.summary.total_credits = root.amount
        return root

    def _entity_tds(self) -> Iterable[tuple]:
        for s in self.case.income.salary:
            yield s, s.employer_name, s.tds
        for i in self.case.income.interest:
            yield i, i.payer_name, i.tds
        for d in self.case.income.dividend:
            yield d, d.payer_name, d.tds
        for b in self.case.income.business:
            yield b, b.description, b.tds
        for r in self.case.income.rental:
            yield r, r.tenant_name or r.property_name, r.tenant_tds
        for o in self.case.income.other_sources:
            yield o, o.description, o.tds

    # ------------------------------------------------------------------ interest 234A/B/C
    def compute_interest(self) -> Line:
        root = Line(id="interest", label="Interest for delay / shortfall (estimate)", kind="interest",
                    rule_ref=self.ref("Sections 234A, 234B, 234C"), notes=[f"Estimated as of {self.ctx.as_of.isoformat()}; assumes no return filed yet."])
        s = self.summary
        assessed = max(ZERO, s.total_tax_liability - s.tds - s.tcs)
        adv_paid = s.advance_tax
        as_of = self.ctx.as_of
        # 234A – delay in filing
        i234a = ZERO
        due = self.rules.due_date_individual
        if as_of > due:
            base = rule_119a(max(ZERO, assessed - adv_paid - sum((money(p.amount.amount) for p in self.case.tax_payments if p.kind == "SELF_ASSESSMENT_TAX" and p.paid_on <= due), ZERO)))
            m = months_ceil(date.fromordinal(due.toordinal() + 1), as_of)
            i234a = money(base * self.rules.interest_rate_234a * m)
            root.add(Line(id="interest.234a", label="234A – late filing", amount=i234a, kind="interest",
                          formula=f"1% × {m} month(s) × {fmt(base)} (due date {due.isoformat()})"))
        exempt_adv = self.ctx.is_senior and self.ctx.is_resident and not self.ctx.has_business_income
        i234b = ZERO
        i234c = ZERO
        if assessed >= self.rules.advance_tax_threshold and not exempt_adv:
            if adv_paid < assessed * Decimal("0.9"):
                base = rule_119a(assessed - adv_paid)
                start = date(self.rules.fy_end.year, 4, 1)
                m = months_ceil(start, as_of)
                i234b = money(base * self.rules.interest_rate_234b * m)
                root.add(Line(id="interest.234b", label="234B – shortfall in advance tax", amount=i234b, kind="interest",
                              formula=f"1% × {m} month(s) × {fmt(base)} (advance tax paid {fmt(adv_paid)} < 90% of {fmt(assessed)})"))
            schedule = [self.rules.presumptive_instalment] if self.ctx.has_presumptive_only else self.rules.advance_tax_instalments
            c_lines = []
            for idx, inst in enumerate(schedule):
                paid_by = sum((money(p.amount.amount) for p in self.case.tax_payments if p.kind == "ADVANCE_TAX" and p.paid_on <= inst.due_on), ZERO)
                required = money(assessed * inst.cumulative_pct)
                # 12% / 36% tolerance for the first two instalments
                tolerance = {0: Decimal("0.12"), 1: Decimal("0.36")}.get(idx)
                if tolerance is not None and len(schedule) > 1 and paid_by >= assessed * tolerance:
                    continue
                shortfall = rule_119a(max(ZERO, required - paid_by))
                if shortfall > 0:
                    amt = money(shortfall * self.rules.interest_rate_234c * inst.interest_months)
                    i234c += amt
                    c_lines.append(Line(id=f"interest.234c.{idx}", label=f"Instalment due {inst.due_on.isoformat()} ({pct(inst.cumulative_pct)})", amount=amt, kind="interest",
                                        formula=f"1% × {inst.interest_months} month(s) × {fmt(shortfall)}"))
            if c_lines:
                root.add(Line(id="interest.234c", label="234C – deferment of advance tax", amount=money(i234c), kind="interest", children=c_lines,
                              notes=["Capital gains / dividend income received after an instalment date is not modelled separately."]))
        elif exempt_adv:
            root.notes.append("Resident senior citizen without business income – advance-tax interest (234B/234C) not applicable.")
        s.interest_234a, s.interest_234b, s.interest_234c = i234a, money(i234b), money(i234c)
        s.total_interest = money(i234a + i234b + i234c)
        root.amount = s.total_interest
        return root

    # ------------------------------------------------------------------ orchestrate
    def run(self) -> RegimeComputation:
        salary = self.compute_salary()
        hp = self.compute_house_property()
        cg = self.compute_capital_gains()
        bus = self.compute_business()
        os_ = self.compute_other_sources()
        exempt_total = sum((money(e.amount.amount) for e in self.case.income.exempt), ZERO)
        self.summary.exempt_income = exempt_total
        # inter-head set-off of business loss (not against salary)
        bus_amt = bus.amount
        heads_positive = salary.amount + max(ZERO, hp.amount) + cg.amount + os_.amount
        gti = salary.amount + hp.amount + cg.amount + bus_amt + os_.amount
        if bus_amt < 0:
            absorbable = min(-bus_amt, max(ZERO, hp.amount) + cg.amount + os_.amount)
            gti = salary.amount + hp.amount + cg.amount + os_.amount - absorbable
            self.warnings.append(f"Business loss of {fmt(-bus_amt)}: {fmt(absorbable)} set off against non-salary income.")
        gti = money(max(gti, ZERO)) if gti >= 0 else money(gti)
        gti_line = Line(id="gti", label="Gross total income", amount=gti, kind="subtotal",
                        formula=f"salary {fmt(salary.amount)} + house property {fmt(hp.amount)} + capital gains {fmt(cg.amount)} + business {fmt(bus_amt)} + other sources {fmt(os_.amount)}",
                        rule_ref=self.ref("Section 14"), status=ValueStatus.CALCULATED)
        self.summary.gross_total_income = gti
        special_income = sum((b["amount"] for b in self.special_buckets.values() if b["rate"] is not None and b["amount"] > 0), ZERO)
        via = self.compute_deductions(gti, special_income)
        taxable = round_to_ten(max(ZERO, gti - via.amount))
        ti_line = Line(id="taxable_income", label="Total (taxable) income", amount=taxable, kind="result",
                       formula=f"{fmt(gti)} − {fmt(via.amount)}, rounded to nearest ₹10 u/s 288A", rule_ref=self.ref("Section 288A"), status=ValueStatus.CALCULATED)
        self.summary.taxable_income = taxable
        tax_lines = self.compute_tax(taxable)
        credits = self.compute_credits()
        interest = self.compute_interest()
        net = money(self.summary.total_tax_liability + self.summary.total_interest - self.summary.total_credits)
        net_rounded = round_to_ten(net)
        self.summary.net_payable = net_rounded
        self.summary.refund_due = -net_rounded if net_rounded < 0 else ZERO
        self.summary.balance_payable = net_rounded if net_rounded > 0 else ZERO
        if gti > 0:
            self.summary.effective_tax_rate = money(self.summary.total_tax_liability / gti * 100)
        result = Line(id="net", label=("Estimated refund" if net_rounded < 0 else "Estimated balance payable"), amount=abs(net_rounded), kind="result",
                      formula=f"total tax {fmt(self.summary.total_tax_liability)} + interest {fmt(self.summary.total_interest)} − taxes paid {fmt(self.summary.total_credits)}, rounded u/s 288B",
                      rule_ref=self.ref("Section 288B"), status=ValueStatus.CALCULATED)
        if exempt_total > 0:
            gti_line.notes.append(f"Exempt income of {fmt(exempt_total)} reported for disclosure only (Schedule EI).")
        self.lines = [salary, hp, cg, bus, os_, gti_line, via, ti_line, *tax_lines, credits, interest, result]
        return RegimeComputation(regime=self.regime.code, label=self.regime.label, summary=self.summary, lines=self.lines,
                                 warnings=self.warnings, assumptions=self.assumptions, credit_ledger=self.credit_ledger)


# ============================================================================= public API


def validate_case(case: TaxCase, rules: AYRules, ctx: TaxContext) -> list[str]:
    warnings: list[str] = []
    tp = case.taxpayer
    if tp.date_of_birth is None:
        warnings.append("Date of birth missing – age-based exemption limits assume a taxpayer below 60.")
    if tp.residential_status != "RESIDENT" and tp.residential_status.value != "RESIDENT":
        warnings.append("Non-resident / RNOR status: rebate u/s 87A and age-based exemptions are not applied; foreign income rules are not modelled.")
    for s in case.income.salary:
        if s.gross_salary.amount <= 0:
            warnings.append(f"Salary from {s.employer_name} is zero – confirm the Form 16 figures.")
        if s.rent_paid and s.rent_paid.amount > 0 and not s.hra_received:
            warnings.append(f"Rent paid recorded for {s.employer_name} but HRA received is unknown – HRA exemption cannot be computed.")
    for d in case.deductions:
        if d.section == "80TTB" and not ctx.is_senior:
            warnings.append("80TTB claimed but the taxpayer is not a senior citizen – ignored.")
    for t in case.income.capital_gains:
        if t.acquisition_date and t.acquisition_date > t.transfer_date:
            warnings.append(f"Capital-gains item '{t.description}': acquisition date is after the transfer date.")
    if len(case.income.rental) > 1 and any(p.is_self_occupied for p in case.income.rental):
        so = [p for p in case.income.rental if p.is_self_occupied]
        if len(so) > 2:
            warnings.append("More than two self-occupied properties – only two can be treated as self-occupied.")
    return warnings


def compute(case: TaxCase, as_of: date | None = None) -> TaxComputation:
    rules = get_rules(case.assessment_year)
    ctx = build_context(case, rules, as_of)
    old = RegimeEngine(case, rules, rules.old, ctx).run()
    new = RegimeEngine(case, rules, rules.new, ctx).run()
    comparison = compare(old, new, rules)
    return TaxComputation(
        case_id=case.id,
        assessment_year=rules.assessment_year,
        financial_year=rules.financial_year,
        rules_version=rules.version,
        rules_status=rules.status,
        as_of=ctx.as_of.isoformat(),
        age_category=ctx.age_category.value,
        residential_status=ctx.residential_status.value,
        old=old,
        new=new,
        comparison=comparison,
        statutory_default_regime=rules.default_regime,
        validation_warnings=validate_case(case, rules, ctx),
    )


def compare(old: RegimeComputation, new: RegimeComputation, rules: AYRules) -> RegimeComparison:
    o, n = old.summary, new.summary
    fields = [
        ("gross_total_income", "Gross total income"),
        ("total_deductions", "Deductions (Chapter VI-A)"),
        ("taxable_income", "Taxable income"),
        ("tax_before_rebate", "Income tax before rebate"),
        ("rebate_87a", "Rebate u/s 87A"),
        ("surcharge", "Surcharge"),
        ("cess", "Health & education cess"),
        ("total_tax_liability", "Total tax liability"),
        ("total_credits", "TDS / taxes paid"),
        ("total_interest", "Interest 234A/B/C"),
        ("net_payable", "Estimated payable (−refund)"),
    ]
    rows = [ComparisonRow(key=k, label=lbl, old=getattr(o, k), new=getattr(n, k), difference=money(getattr(n, k) - getattr(o, k))) for k, lbl in fields]
    diff = money(n.total_tax_liability - o.total_tax_liability)
    if diff < 0:
        lower = "NEW"
    elif diff > 0:
        lower = "OLD"
    else:
        lower = "EQUAL"
    explanation: list[str] = []
    ded_diff = o.total_deductions - n.total_deductions
    if ded_diff > 0:
        explanation.append(f"The old regime allows {fmt(ded_diff)} more in Chapter VI-A deductions (80C, 80D, 80TTA…) than the new regime.")
    sal_diff = n.income_salary - o.income_salary
    if sal_diff != 0:
        explanation.append(f"Salary income differs by {fmt(abs(sal_diff))}: the new regime has a higher standard deduction ({fmt(rules.new.standard_deduction_salary)} vs {fmt(rules.old.standard_deduction_salary)}) but no HRA/LTA exemption or professional-tax deduction.")
    hp_diff = n.income_house_property - o.income_house_property
    if hp_diff != 0:
        explanation.append(f"House-property income differs by {fmt(abs(hp_diff))}: self-occupied interest and loss set-off are not available in the new regime.")
    if o.taxable_income != n.taxable_income:
        explanation.append(f"Taxable income: {fmt(o.taxable_income)} (old) vs {fmt(n.taxable_income)} (new).")
    explanation.append(f"Slab tax before rebate: {fmt(o.tax_before_rebate)} (old) vs {fmt(n.tax_before_rebate)} (new) – the new regime has wider, lower-rate slabs.")
    if o.rebate_87a != n.rebate_87a:
        explanation.append(f"Rebate u/s 87A: {fmt(o.rebate_87a)} (old, income ≤ {fmt(rules.old.rebate.income_threshold)}) vs {fmt(n.rebate_87a)} (new, income ≤ {fmt(rules.new.rebate.income_threshold)} with marginal relief).")
    if lower == "EQUAL":
        explanation.append("Both regimes produce the same total tax liability with the current data.")
    else:
        explanation.append(f"With the current data the {lower.lower()} regime results in {fmt(abs(diff))} lower total tax. Astra does not select a regime for you – review the assumptions and confirm your choice in the return review.")
    assumptions = list(dict.fromkeys(old.assumptions + new.assumptions))
    assumptions.append(f"The statutory default regime is the {rules.default_regime.lower()} regime; salaried taxpayers may choose either regime each year, taxpayers with business income face restrictions on switching back (Form 10-IEA).")
    return RegimeComparison(rows=rows, lower_tax_regime=lower, difference=abs(diff), explanation=explanation, assumptions=assumptions,
                            statutory_default=rules.default_regime)


def explain_line(computation: TaxComputation, regime: str, line_id: str) -> dict | None:
    """Return the line, its ancestors (path) and children for the "Why is this number here?" panel."""
    rc = computation.regime(regime)
    path: list[Line] = []

    def walk(line: Line, trail: list[Line]) -> bool:
        trail.append(line)
        if line.id == line_id:
            path.extend(trail)
            return True
        for c in line.children:
            if walk(c, trail):
                return True
        trail.pop()
        return False

    for top in rc.lines:
        if walk(top, []):
            break
    if not path:
        return None
    target = path[-1]

    def rolled_sources(line: Line) -> list[EvidenceRef]:
        """A subtotal inherits the provenance of its descendants (deduplicated) so the trail never dead-ends."""
        if line.sources:
            return line.sources
        seen: set[tuple] = set()
        out: list[EvidenceRef] = []
        for c in line.children:
            for s in rolled_sources(c):
                key = (s.label, s.document_id, s.entity_id)
                if key not in seen:
                    seen.add(key)
                    out.append(s)
        return out

    line_dump = target.model_dump(mode="json")
    if not target.sources:
        line_dump["sources"] = [s.model_dump(mode="json") for s in rolled_sources(target)]
    return {
        "regime": regime,
        "line": line_dump,
        "path": [{"id": l.id, "label": l.label, "amount": float(l.amount)} for l in path[:-1]],
        "inputs": [{"id": c.id, "label": c.label, "amount": float(c.amount), "formula": c.formula, "status": c.status.value if c.status else None,
                    "sources": [s.model_dump(mode="json") for s in rolled_sources(c)]} for c in target.children],
        "rules_version": computation.rules_version,
    }
