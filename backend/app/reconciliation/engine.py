"""Cross-source reconciliation engine.

Compares what the taxpayer's return currently contains (income entities, TDS ledger) with every
observation extracted from documents (Form 16/16A, 26AS, AIS, TIS, bank/broker statements, previous ITR)
and with the onboarding profile. Emits neutral, evidence-backed ``ReconciliationItem``s of five kinds:
MISSING, MISMATCH, DUPLICATE, CLASSIFICATION, TIMING. It never accuses anyone – items "require review".

Every counterparty (employer, bank, company, client, tenant) is identified by a normalised *name key*;
TANs are mapped onto the same key so Form 16, 26AS, AIS, certificates and statements line up.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from decimal import Decimal

from app.documents.extractors.common import normalize_key
from app.models.common import SOURCE_LABELS, EvidenceRef, SourceType, TracedValue, fy_bounds, money, utcnow
from app.models.tax_model import ReconciliationItem, SuggestedResolution, TaxCase
from app.reconciliation import impact as imp
from app.tax_engine.engine import fmt, months_between
from app.tax_engine.rules import get_rules

ZERO = Decimal("0")
TOL_ABS = Decimal("10")
TOL_REL = Decimal("0.005")


def msum(it) -> Decimal:
    return money(sum((money(x) for x in it), ZERO))


def differs(a, b) -> bool:
    a, b = money(a), money(b)
    return abs(a - b) > max(TOL_ABS, abs(max(a, b)) * TOL_REL)


def ev(o: dict, label: str | None = None, amount=None) -> EvidenceRef:
    st = SourceType(o.get("source_type", "OTHER"))
    return EvidenceRef(label=label or f"{SOURCE_LABELS[st]} · {o.get('reference', '')}", source_type=st, document_id=o.get("document_id"),
                       reference=o.get("reference"), amount=money(amount if amount is not None else (o.get("amount") or 0)), period=o.get("period") or o.get("date"))


def ev_entity(entity, label: str, amount, tv: TracedValue | None = None) -> EvidenceRef:
    tv = tv or getattr(entity, "amount", None) or getattr(entity, "gross_salary", None) or getattr(entity, "tax_deducted", None)
    st = SourceType.USER_INPUT
    doc_id = None
    ref = None
    if tv is not None and tv.provenance:
        st = tv.provenance[0].source_type
        doc_id = tv.provenance[0].document_id
        ref = tv.provenance[0].reference
    return EvidenceRef(label=f"{label} · {SOURCE_LABELS[st]}" + (f" · {ref}" if ref else ""), source_type=st, document_id=doc_id, entity_id=entity.id, reference=ref, amount=money(amount))


def ev_tds(t) -> EvidenceRef:
    return EvidenceRef(label=f"{SOURCE_LABELS[t.source_type]} · {t.deductor_name} · section {t.section}", source_type=t.source_type,
                       document_id=(t.document_ids[0] if t.document_ids else None), entity_id=t.id, reference=f"section {t.section}", amount=money(t.tax_deducted.amount), period=t.period)


class Reconciler:
    def __init__(self, case: TaxCase):
        self.case = case
        self.rules = get_rules(case.assessment_year)
        self.fy_start, self.fy_end = fy_bounds(case.assessment_year)
        self.items: list[ReconciliationItem] = []
        self.obs: list[dict] = [o for d in case.documents for o in d.observations]
        self._fingerprints: set[str] = set()
        self.tan_map: dict[str, str] = {}
        self.names: dict[str, str] = {}
        for o in self.obs:
            name = o.get("counterparty")
            key = normalize_key(name) if name else (o.get("counterparty_key") or "")
            o["ckey"] = key
            if key and name and key not in self.names:
                self.names[key] = name
            if o.get("counterparty_tan") and key:
                self.tan_map[o["counterparty_tan"]] = key
        for t in case.tax_deducted:
            k = normalize_key(t.deductor_name)
            if t.deductor_tan and k:
                self.tan_map.setdefault(t.deductor_tan, k)
                self.names.setdefault(k, t.deductor_name)
        for s in case.income.salary:
            if s.employer_tan:
                self.tan_map.setdefault(s.employer_tan, normalize_key(s.employer_name))

    # ------------------------------------------------------------------ utilities
    def key_of(self, name: str | None, tan: str | None = None) -> str:
        if tan and tan in self.tan_map:
            return self.tan_map[tan]
        return normalize_key(name)

    def display(self, key: str, fallback: str | None = None) -> str:
        n = self.names.get(key) or fallback or key
        return n.title() if n.isupper() else n

    def by_category(self, *cats: str) -> list[dict]:
        return [o for o in self.obs if o.get("category") in cats]

    def covered(self, key: str, *categories: str) -> bool:
        """True when an item for this counterparty already exists in one of the categories."""
        return any(i.category in categories and (f":{key}" in i.fingerprint or i.fingerprint.split(":")[2].startswith(key)) for i in self.items)

    def emit(self, *, kind: str, category: str, key: str, severity: str, title: str, description: str, evidence: list[EvidenceRef], required_action: str,
             entity_ids: list[str] | None = None, impact=None, impact_note: str | None = None, resolutions: list[SuggestedResolution] | None = None) -> ReconciliationItem | None:
        fp = f"{kind}:{category}:{key}"
        digest = hashlib.sha1(fp.encode()).hexdigest()[:16]
        if digest in self._fingerprints:
            return None
        self._fingerprints.add(digest)
        item = ReconciliationItem(fingerprint=f"{fp}#{digest}", kind=kind, severity=severity, category=category, title=title, description=description, evidence=evidence,
                                  entity_ids=entity_ids or [], impact_estimate=(money(impact) if impact is not None else None), impact_note=impact_note,
                                  required_action=required_action, suggested_resolutions=resolutions or [])
        self.items.append(item)
        return item

    def safe_delta(self, mutate) -> Decimal | None:
        try:
            return imp.tax_delta(self.case, mutate)
        except Exception:  # noqa: BLE001 – impact is best-effort
            return None

    def tds_entries(self, key: str, section_prefix: str | None = None) -> list:
        out = []
        for t in self.case.tax_deducted:
            if self.key_of(t.deductor_name, t.deductor_tan) != key:
                continue
            if section_prefix and not t.section.replace("-", "").upper().startswith(section_prefix):
                continue
            out.append(t)
        return out

    # ------------------------------------------------------------------ salary
    def reconcile_salary(self) -> None:
        case = self.case
        declared = {self.key_of(s.employer_name, s.employer_tan): s for s in case.income.salary}
        sources: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for o in self.by_category("SALARY", "AIS_SALARY"):
            sources[o["ckey"]][o["source_type"]].append(o)
        for t in case.tax_deducted:
            if t.section.replace("-", "") == "192" and t.source_type in (SourceType.FORM26AS, SourceType.AIS):
                sources[self.key_of(t.deductor_name, t.deductor_tan)]["LEDGER:" + t.source_type.value].append(
                    {"source_type": t.source_type.value, "document_id": (t.document_ids[0] if t.document_ids else None), "reference": f"Part I · section 192",
                     "amount": float(t.amount_paid_credited.amount), "tax_deducted": float(t.tax_deducted.amount), "counterparty": t.deductor_name, "counterparty_tan": t.deductor_tan, "ckey": self.key_of(t.deductor_name, t.deductor_tan)})
        for key, per_source in sources.items():
            entity = declared.get(key)
            name = self.display(key, next((o.get("counterparty") for lst in per_source.values() for o in lst if o.get("counterparty")), key))
            if entity is None:
                evidence = [ev(o) for lst in per_source.values() for o in lst]
                amount_paid = max((money(o.get("amount") or 0) for lst in per_source.values() for o in lst), default=ZERO)
                tds = max((money(o.get("tax_deducted") or 0) for lst in per_source.values() for o in lst), default=ZERO)
                tan = next((o.get("counterparty_tan") for lst in per_source.values() for o in lst if o.get("counterparty_tan")), None)
                if amount_paid <= 0:
                    continue
                delta = self.safe_delta(imp.add_salary(name, tan, amount_paid, tds))
                self.emit(kind="MISSING", category="SALARY", key=key, severity="HIGH", title=f"Salary from {name} not accounted for",
                          description=f"Form 26AS / AIS report salary of {fmt(amount_paid)} (TDS {fmt(tds)}) from {name}, but the return has no salary entry or Form 16 for this employer. This item requires review.",
                          evidence=evidence, required_action="Upload the Form 16 for this employer, or confirm the salary and TDS to add them.",
                          impact=delta, impact_note="Estimated change in total tax if the salary (and its TDS credit) is added.",
                          resolutions=[SuggestedResolution(action="UPLOAD_DOCUMENT", label="Upload Form 16", payload={"type": "FORM16"}),
                                       SuggestedResolution(action="ADD_INCOME", label=f"Add salary {fmt(amount_paid)} from {name}",
                                                           payload={"head": "SALARY", "employer_name": name, "employer_tan": tan, "gross_salary": float(amount_paid), "tds": float(tds)})])
                continue
            declared_amt = money(entity.gross_salary.amount)
            for src, lst in per_source.items():
                if src.startswith("LEDGER"):
                    continue
                for o in lst:
                    if o["source_type"] == "FORM16":
                        continue
                    if differs(o.get("amount") or 0, declared_amt):
                        st = SourceType(o["source_type"])
                        self.emit(kind="MISMATCH", category="SALARY", key=f"{key}:{src}", severity="MEDIUM", title=f"Salary from {name} differs between {SOURCE_LABELS[st]} and the return",
                                  description=f"{SOURCE_LABELS[st]} reports {fmt(o.get('amount') or 0)} while the return shows {fmt(declared_amt)}. AIS salary can include exempt components – this item requires review.",
                                  evidence=[ev(o), ev_entity(entity, "Gross salary", declared_amt, entity.gross_salary)], entity_ids=[entity.id],
                                  required_action="Compare the Form 16 Part B figure with the AIS entry and confirm which is correct.",
                                  resolutions=[SuggestedResolution(action="CONFIRM_ENTITY", label="Confirm Form 16 figure", payload={"entity_id": entity.id})])

    # ------------------------------------------------------------------ interest
    def reconcile_interest(self) -> None:
        case = self.case
        declared_by_bank: dict[str, list] = defaultdict(list)
        for i in case.income.interest:
            declared_by_bank[self.key_of(i.payer_name, i.payer_tan)].append(i)
        # --- duplicates inside the return
        dup_declared: dict[tuple, Decimal] = {}
        seen: dict[tuple, list] = defaultdict(list)
        for i in case.income.interest:
            seen[(self.key_of(i.payer_name, i.payer_tan), i.kind, money(i.amount.amount))].append(i)
        for (bank_key, kind, amount), lst in seen.items():
            if len(lst) > 1:
                dup = sorted(lst, key=lambda x: 0 if x.status.value in ("EXTRACTED", "USER_CONFIRMED") else 1)
                remove = dup[-1]
                dup_declared[(bank_key, kind)] = money(amount * (len(lst) - 1))
                delta = self.safe_delta(imp.remove_entity(remove.id))
                self.emit(kind="DUPLICATE", category="INTEREST", key=f"{bank_key}:{kind}:{amount}", severity="MEDIUM", title=f"{kind.replace('_', ' ').title()} interest from {lst[0].payer_name} appears twice",
                          description=f"Two entries of {fmt(amount)} exist for {lst[0].payer_name} ({kind.replace('_', ' ').lower()}): " + " and ".join(self._entry_source(x) for x in lst) + ". The same income appears to have been reported by two sources.",
                          evidence=[ev_entity(x, f"Interest entry ({self._entry_source(x)})", x.amount.amount) for x in lst], entity_ids=[x.id for x in lst],
                          impact=delta, impact_note="Estimated change in total tax if the duplicate entry is removed.", required_action="Keep one entry (prefer the certificate) and remove the other.",
                          resolutions=[SuggestedResolution(action="REMOVE_DUPLICATE", label=f"Remove the {self._entry_source(remove)} entry", payload={"entity_id": remove.id})])
        # --- observations per bank & kind
        bank_obs: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for o in self.by_category("BANK_CREDIT"):
            if o.get("kind") == "INTEREST" and not o.get("out_of_period"):
                bank_obs[o["ckey"]][o.get("sub_kind") or "SAVINGS"].append(o)
        ais_obs: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for o in self.by_category("AIS_INTEREST"):
            ais_obs[o["ckey"]][o.get("sub_kind") or "OTHER"].append(o)
        cert_obs: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for o in self.by_category("INTEREST"):
            cert_obs[o["ckey"]][o.get("sub_kind") or "OTHER"].append(o)
        dup_rows: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for o in self.by_category("BANK_DUPLICATE"):
            dup_rows[o["ckey"]] += money(o.get("amount") or 0)
        ledger_keys = {self.key_of(t.deductor_name, t.deductor_tan) for t in case.tax_deducted if t.section.replace("-", "").upper().startswith("194A")}
        for key in set(bank_obs) | set(ais_obs) | set(cert_obs) | ledger_keys:
            bank_name = self.display(key)
            declared = declared_by_bank.get(key, [])
            tds_ledger = self.tds_entries(key, "194A")
            for kind in ("SAVINGS", "FIXED_DEPOSIT", "RECURRING_DEPOSIT", "OTHER"):
                bank_amt = msum(o.get("amount") or 0 for o in bank_obs[key][kind])
                ais_list = ais_obs[key][kind] if kind != "OTHER" else ais_obs[key]["OTHER"] + ais_obs[key]["INCOME_TAX_REFUND"]
                ais_amt = msum(o.get("amount") or 0 for o in ais_list)
                cert_amt = msum(o.get("amount") or 0 for o in cert_obs[key][kind])
                ledger_amt = msum(t.amount_paid_credited.amount for t in tds_ledger) if kind == "FIXED_DEPOSIT" else ZERO
                ledger_tds = msum(t.tax_deducted.amount for t in tds_ledger) if kind == "FIXED_DEPOSIT" else ZERO
                declared_kind = [i for i in declared if i.kind == kind]
                declared_amt = msum(i.amount.amount for i in declared_kind) - dup_declared.get((key, kind), ZERO)
                third_party = ais_amt or cert_amt or ledger_amt or bank_amt
                if kind == "FIXED_DEPOSIT" and bank_amt > 0 and (ais_amt or ledger_amt) and bank_amt < (ais_amt or ledger_amt):
                    bank_amt = ais_amt or ledger_amt  # statement credits are net of TDS
                if third_party <= 0:
                    continue
                if not declared_kind:
                    evidence = [ev(o) for o in bank_obs[key][kind][:4]] + [ev(o) for o in ais_list] + [ev(o) for o in cert_obs[key][kind]] + ([ev_tds(t) for t in tds_ledger] if kind == "FIXED_DEPOSIT" else [])
                    tds = max(msum(o.get("tax_deducted") or 0 for o in ais_list), ledger_tds)
                    delta = self.safe_delta(imp.add_interest(bank_name, third_party, kind, tds=tds))
                    sev = "HIGH" if (tds > 0 or third_party >= 50000) else ("MEDIUM" if third_party >= 1000 else "LOW")
                    label = kind.replace("_", " ").lower()
                    src_label = "Bank statement" if bank_obs[key][kind] else ("AIS" if ais_list else ("Interest certificate" if cert_obs[key][kind] else "Form 26AS"))
                    extra = []
                    if ais_amt and bank_obs[key][kind]:
                        extra.append(f"AIS reports {fmt(ais_amt)}")
                    if tds > 0:
                        extra.append(f"TDS of {fmt(tds)} was deducted u/s 194A")
                    self.emit(kind="MISSING", category="INTEREST", key=f"{key}:{kind}", severity=sev, title=f"{label.capitalize()} interest from {bank_name} not accounted for",
                              description=f"{src_label} shows {fmt(third_party)} of {label} interest income that has not yet been accounted for" + (f" ({'; '.join(extra)})" if extra else "") + ". This item requires review.",
                              evidence=evidence, impact=delta, impact_note="Estimated change in total tax if this interest is added.",
                              required_action="Confirm the interest to add it to income from other sources, or upload the bank's interest certificate.",
                              resolutions=[SuggestedResolution(action="ADD_INCOME", label=f"Add {label} interest {fmt(third_party)} from {bank_name}",
                                                               payload={"head": "INTEREST", "payer_name": bank_name, "kind": kind, "amount": float(third_party), "tds": float(tds),
                                                                        "account_ref": (ais_list or bank_obs[key][kind] or [{}])[0].get("account_ref"), "payer_tan": next((t.deductor_tan for t in tds_ledger), None)}),
                                           SuggestedResolution(action="UPLOAD_DOCUMENT", label="Upload interest certificate", payload={"type": "INTEREST_CERTIFICATE"})])
                    continue
                for label, amount, obs_list in (("AIS", ais_amt, ais_list), ("Bank statement", bank_amt, bank_obs[key][kind])):
                    if amount <= 0 or not differs(amount, declared_amt):
                        continue
                    if label == "Bank statement" and (kind == "FIXED_DEPOSIT" or (dup_rows.get(key) and not differs(amount - dup_rows[key], declared_amt))):
                        continue
                    delta = self.safe_delta(imp.adjust_interest_amount(declared_kind[0].id, amount))
                    self.emit(kind="MISMATCH", category="INTEREST", key=f"{key}:{kind}:{label}", severity="MEDIUM", title=f"{kind.replace('_', ' ').title()} interest from {bank_name} differs between {label} and the return",
                              description=f"{label} reports {fmt(amount)} while the return shows {fmt(declared_amt)}" + (" – AIS may report accrued rather than paid interest" if label == "AIS" else "") + ". This item requires review.",
                              evidence=[ev(o) for o in obs_list[:4]] + [ev_entity(i, "Interest entry", i.amount.amount) for i in declared_kind], entity_ids=[i.id for i in declared_kind],
                              impact=delta, impact_note=f"Estimated change in total tax if the {label} figure is used instead.", required_action=f"Confirm which figure is correct; if the {label} figure is right, update the entry.",
                              resolutions=[SuggestedResolution(action="CONFIRM_ENTITY", label="Keep the current figure", payload={"entity_id": declared_kind[0].id}),
                                           SuggestedResolution(action="RECLASSIFY", label=f"Use the {label} figure {fmt(amount)}", payload={"entity_id": declared_kind[0].id, "amount": float(amount)})])
        # --- duplicated rows inside a statement
        for o in self.by_category("BANK_DUPLICATE"):
            amount = money(o.get("amount") or 0)
            delta = self.safe_delta(imp.add_interest(o.get("counterparty") or "Bank", amount, "SAVINGS"))
            self.emit(kind="DUPLICATE", category="INTEREST", key=f"{o['ckey']}:{o.get('reference')}", severity="LOW", title=f"Duplicate credit in the {o.get('counterparty')} statement",
                      description=f"{o.get('reference')} appears twice in the statement – it repeats an earlier credit of {fmt(amount)} with the same date and narration ('{o.get('narration')}'). If interest is added from the statement, this row should be counted only once.",
                      evidence=[ev(o)], impact=delta, impact_note="Tax that would be over-paid if the duplicate were counted as income.", required_action="Confirm that this is a statement duplicate and not a second credit.",
                      resolutions=[SuggestedResolution(action="REVIEW", label="Mark as duplicate row", payload={})])
        # --- timing: credits outside the financial year
        for o in self.by_category("BANK_CREDIT"):
            if o.get("kind") == "INTEREST" and o.get("out_of_period"):
                amount = money(o.get("amount") or 0)
                delta = self.safe_delta(imp.add_interest(o.get("counterparty") or "Bank", amount, o.get("sub_kind") or "OTHER"))
                self.emit(kind="TIMING", category="INTEREST", key=f"{o['ckey']}:{o.get('reference')}", severity="LOW", title=f"Interest credit dated outside FY {self.rules.financial_year}",
                          description=f"{o.get('reference')} of {fmt(amount)} is dated {o.get('date')}, which falls outside the financial year. Transaction date and reporting period appear inconsistent; it belongs to the year in which it accrued.",
                          evidence=[ev(o)], impact=delta, impact_note="Tax effect if this credit were included in the current year.", required_action="Confirm the accrual period with the bank's certificate; exclude it from this year if it belongs to the next.",
                          resolutions=[SuggestedResolution(action="REVIEW", label="Exclude from this year", payload={})])

    @staticmethod
    def _entry_source(i) -> str:
        if i.amount.provenance:
            return SOURCE_LABELS[i.amount.provenance[0].source_type]
        return "user entry"

    # ------------------------------------------------------------------ dividends
    def reconcile_dividends(self) -> None:
        case = self.case
        declared: dict[str, list] = defaultdict(list)
        for d in case.income.dividend:
            declared[normalize_key(d.payer_name)].append(d)
        ais: dict[str, list[dict]] = defaultdict(list)
        for o in self.by_category("AIS_DIVIDEND"):
            ais[o["ckey"]].append(o)
        for key, lst in ais.items():
            name = self.display(key, lst[0].get("counterparty"))
            ais_amt = msum(o.get("amount") or 0 for o in lst)
            tds = msum(o.get("tax_deducted") or 0 for o in lst)
            if key not in declared:
                delta = self.safe_delta(imp.add_dividend(name, ais_amt, tds))
                self.emit(kind="MISSING", category="DIVIDEND", key=key, severity="MEDIUM" if ais_amt >= 5000 else "LOW", title=f"Dividend from {name} not accounted for",
                          description=f"AIS reports dividend of {fmt(ais_amt)} from {name} that is not in the return. This item requires review.", evidence=[ev(o) for o in lst] + [ev_tds(t) for t in self.tds_entries(key, "194")],
                          impact=delta, impact_note="Estimated change in total tax if the dividend is added.", required_action="Confirm the dividend (check your demat / bank credits) and add it.",
                          resolutions=[SuggestedResolution(action="ADD_INCOME", label=f"Add dividend {fmt(ais_amt)} from {name}", payload={"head": "DIVIDEND", "payer_name": name, "amount": float(ais_amt), "tds": float(tds)})])
                continue
            decl_amt = msum(d.amount.amount for d in declared[key])
            if differs(ais_amt, decl_amt):
                diff = money(ais_amt - decl_amt)
                delta = self.safe_delta(imp.add_dividend(name, diff)) if diff > 0 else None
                self.emit(kind="MISMATCH", category="DIVIDEND", key=key, severity="MEDIUM", title=f"Dividend from {name} differs between AIS and the dividend statement",
                          description=f"AIS reports {fmt(ais_amt)} while the return (from your statement) shows {fmt(decl_amt)}. AIS can include holdings in another demat account. This item requires review.",
                          evidence=[ev(o) for o in lst] + [ev_entity(d, "Dividend entry", d.amount.amount) for d in declared[key]], entity_ids=[d.id for d in declared[key]],
                          impact=delta, impact_note="Estimated change in total tax if the AIS figure is used.", required_action="Check all demat accounts; if the AIS figure is right, add the difference.",
                          resolutions=[SuggestedResolution(action="ADD_INCOME", label=f"Add the difference {fmt(diff)}", payload={"head": "DIVIDEND", "payer_name": name, "amount": float(diff), "tds": 0.0}) if diff > 0 else SuggestedResolution(action="REVIEW", label="Review", payload={}),
                                       SuggestedResolution(action="CONFIRM_ENTITY", label="Keep statement figure", payload={"entity_id": declared[key][0].id})])

    # ------------------------------------------------------------------ capital gains
    def reconcile_capital_gains(self) -> None:
        case = self.case
        by_isin: dict[str, list] = defaultdict(list)
        for t in case.income.capital_gains:
            by_isin[t.isin or normalize_key(t.description)].append(t)
        ais: dict[str, list[dict]] = defaultdict(list)
        for o in self.by_category("AIS_SECURITIES_SALE"):
            ais[o.get("isin") or normalize_key(o.get("symbol"))].append(o)
        if ais and not case.income.capital_gains:
            total = msum(o.get("amount") or 0 for lst in ais.values() for o in lst)
            self.emit(kind="MISSING", category="CAPITAL_GAINS", key="all", severity="HIGH", title="Sale of securities reported in AIS but no capital gains in the return",
                      description=f"AIS reports sales of securities / mutual-fund units worth {fmt(total)}; the return has no capital-gains transactions. This item requires review.",
                      evidence=[ev(o) for lst in ais.values() for o in lst][:8], required_action="Upload the broker's tax P&L / capital-gains statement.",
                      resolutions=[SuggestedResolution(action="UPLOAD_DOCUMENT", label="Upload broker statement", payload={"type": "BROKER_STATEMENT"})])
        for isin, lst in ais.items():
            trades = by_isin.get(isin, [])
            if not trades:
                continue
            for o in lst:
                if any(not differs(t.sale_consideration.amount, o.get("amount") or 0) for t in trades):
                    continue
                same_date = [t for t in trades if o.get("date") and t.transfer_date.isoformat() == o.get("date")]
                cand = same_date[0] if same_date else (trades[0] if len(trades) == 1 else None)
                if cand is None:
                    continue
                broker_amt = money(cand.sale_consideration.amount)
                ais_amt = money(o.get("amount") or 0)
                diff = money(ais_amt - broker_amt)

                def _use_ais(c, tid=cand.id, v=ais_amt):
                    for t in c.income.capital_gains:
                        if t.id == tid:
                            t.sale_consideration = t.sale_consideration.model_copy(update={"amount": v})
                delta = self.safe_delta(_use_ais)
                self.emit(kind="MISMATCH", category="CAPITAL_GAINS", key=f"{isin}:{o.get('ais_id')}", severity="LOW" if abs(diff) < 25000 else "MEDIUM", title=f"Sale value of {cand.description} differs between AIS and the broker statement",
                          description=f"AIS reports {fmt(ais_amt)} while the broker statement shows {fmt(broker_amt)} (difference {fmt(diff)}). Contract-note values are usually authoritative; this item requires review.",
                          evidence=[ev(o), ev_entity(cand, "Sale consideration", broker_amt, cand.sale_consideration)], entity_ids=[cand.id], impact=delta, impact_note="Estimated change in total tax if the AIS value were used.",
                          required_action="Compare with the contract note; keep the broker value or submit AIS feedback.",
                          resolutions=[SuggestedResolution(action="CONFIRM_ENTITY", label="Keep broker value", payload={"entity_id": cand.id}), SuggestedResolution(action="REVIEW", label="Submit AIS feedback", payload={})])
        raw_types = {o.get("reference"): o for o in self.by_category("CAPITAL_GAIN_TXN")}
        for t in case.income.capital_gains:
            o = raw_types.get(f"Trade {t.broker_ref}") if t.broker_ref else None
            issues = []
            raw = (o or {}).get("raw_type", "") or ""
            if t.asset_class in ("EQUITY_MF", "DEBT_MF") and raw and "EQUITY" not in raw.upper() and "DEBT" not in raw.upper() and not t.holding_override:
                issues.append("the statement does not say whether the fund is equity-oriented or debt (debt-fund gains on units bought after 1 Apr 2023 are taxed at slab rates)")
            if t.acquisition_date and not t.holding_override:
                held = months_between(t.acquisition_date, t.transfer_date)
                threshold = self.rules.holding_months(t.asset_class, t.transfer_date)
                if abs(held - threshold) <= 1:
                    issues.append(f"the holding period ({held} months) is within a month of the {threshold}-month long-term threshold")
            if issues:
                delta = self.safe_delta(imp.set_asset_class(t.id, "DEBT_MF")) if t.asset_class == "EQUITY_MF" else None
                self.emit(kind="CLASSIFICATION", category="CAPITAL_GAINS", key=f"{t.isin or t.id}:class", severity="MEDIUM", title=f"Classification of {t.description} requires confirmation",
                          description=f"A transaction may belong to a different tax treatment: {'; '.join(issues)}. This item requires review.",
                          evidence=[ev_entity(t, "Trade", t.sale_consideration.amount, t.sale_consideration)] + ([ev(o)] if o else []), entity_ids=[t.id], impact=delta,
                          impact_note="Estimated tax difference between the two treatments." if delta is not None else None, required_action="Confirm the fund category / holding period from the fund house statement.",
                          resolutions=[SuggestedResolution(action="RECLASSIFY", label="Confirm as equity-oriented fund", payload={"entity_id": t.id, "asset_class": "EQUITY_MF"}),
                                       SuggestedResolution(action="RECLASSIFY", label="Reclassify as debt fund", payload={"entity_id": t.id, "asset_class": "DEBT_MF"})])

    # ------------------------------------------------------------------ rent
    def reconcile_rent(self) -> None:
        case = self.case
        rent_credits = [o for o in self.by_category("BANK_CREDIT") if o.get("kind") == "RENT"]
        ais_rent = self.by_category("AIS_RENT")
        ledger = [t for t in case.tax_deducted if t.section.replace("-", "").upper() in ("194I", "194IB", "194IA")]
        declared_total = msum(r.annual_rent_received.amount for r in case.income.rental if not r.is_self_occupied)
        if not rent_credits and not ais_rent and not ledger:
            return
        credit_total = msum(o.get("amount") or 0 for o in rent_credits)
        ais_total = msum(o.get("amount") or 0 for o in ais_rent)
        ledger_total = max((money(t.amount_paid_credited.amount) for t in ledger), default=ZERO)
        third = ais_total or ledger_total or credit_total
        if declared_total <= 0 and third > 0:
            tenant = (ais_rent[0].get("counterparty") if ais_rent else (ledger[0].deductor_name if ledger else None))
            if tenant is None and rent_credits:
                narr = rent_credits[0].get("narration", "")
                tenant = narr.split("-")[1].title() if "-" in narr else None
            tds = max(msum(o.get("tax_deducted") or 0 for o in ais_rent), max((money(t.tax_deducted.amount) for t in ledger), default=ZERO))
            key = normalize_key(tenant) or "rent"
            if ais_rent or ledger:
                delta = self.safe_delta(imp.add_rental("Let-out property", third, tenant_tds=tds, tenant=tenant))
                self.emit(kind="MISSING", category="RENTAL", key=key, severity="HIGH", title="Rental income not accounted for",
                          description=(f"AIS reports rent received of {fmt(ais_total)}" if ais_rent else f"Form 26AS shows TDS u/s 194-IB on rent of {fmt(ledger_total)}") + (f" from {self.display(key, tenant)}" if tenant else "") + (f"; {len(rent_credits)} monthly bank credits agree" if rent_credits else "") + ". The return has no let-out property. This item requires review.",
                          evidence=[ev(o) for o in ais_rent] + [ev_tds(t) for t in ledger] + [ev(o) for o in rent_credits[:4]], impact=delta, impact_note="Estimated change in total tax if the rent is added (after the 30% standard deduction).",
                          required_action="Confirm the property and annual rent to add house-property income.",
                          resolutions=[SuggestedResolution(action="ADD_INCOME", label=f"Add rental income {fmt(third)}", payload={"head": "RENTAL", "tenant_name": tenant, "annual_rent": float(third), "tenant_tds": float(tds)})])
            elif rent_credits:
                self.emit(kind="CLASSIFICATION", category="RENTAL", key="recurring-credits", severity="LOW", title="Recurring credits may be rental income",
                          description=f"{len(rent_credits)} monthly credits totalling {fmt(credit_total)} carry a rent narration. A transaction may belong to a different income category and requires confirmation.",
                          evidence=[ev(o) for o in rent_credits[:4]], required_action="Confirm whether these credits are rent.", resolutions=[SuggestedResolution(action="REVIEW", label="Confirm as rent", payload={})])
        elif declared_total > 0 and ais_total > 0 and differs(ais_total, declared_total):
            self.emit(kind="MISMATCH", category="RENTAL", key="ais-vs-declared", severity="MEDIUM", title="Rent received differs between AIS and the return",
                      description=f"AIS reports {fmt(ais_total)} while the return shows {fmt(declared_total)}. This item requires review.", evidence=[ev(o) for o in ais_rent],
                      entity_ids=[r.id for r in case.income.rental], required_action="Confirm the annual rent received.", resolutions=[SuggestedResolution(action="REVIEW", label="Review rent", payload={})])
        elif declared_total > 0 and rent_credits and not ais_rent and credit_total > declared_total and differs(credit_total, declared_total):
            self.emit(kind="MISMATCH", category="RENTAL", key="bank-vs-declared", severity="LOW", title="Rent credits in the bank statement exceed declared rent",
                      description=f"Bank credits tagged as rent total {fmt(credit_total)} while the return shows {fmt(declared_total)}. This item requires review.", evidence=[ev(o) for o in rent_credits[:4]],
                      entity_ids=[r.id for r in case.income.rental], required_action="Confirm the annual rent received.", resolutions=[SuggestedResolution(action="REVIEW", label="Review rent", payload={})])

    # ------------------------------------------------------------------ business / professional receipts
    def reconcile_business(self) -> None:
        case = self.case
        ledger = [t for t in case.tax_deducted if t.section.replace("-", "").upper() in ("194J", "194JA", "194JB", "194H", "194C")]
        ais = self.by_category("AIS_BUSINESS_RECEIPTS")
        if not ledger and not ais:
            return
        per_client: dict[str, dict] = {}
        for t in ledger:
            k = self.key_of(t.deductor_name, t.deductor_tan)
            c = per_client.setdefault(k, {"name": self.display(k, t.deductor_name), "amount": ZERO, "tds": ZERO, "sources": set(), "evidence": [], "tan": t.deductor_tan})
            c["amount"] = max(c["amount"], money(t.amount_paid_credited.amount))
            c["tds"] = max(c["tds"], money(t.tax_deducted.amount))
            c["sources"].add(t.source_type.value)
            c["evidence"].append(ev_tds(t))
        for o in ais:
            k = o["ckey"]
            c = per_client.setdefault(k, {"name": self.display(k, o.get("counterparty")), "amount": ZERO, "tds": ZERO, "sources": set(), "evidence": [], "tan": o.get("counterparty_tan")})
            c["amount"] = max(c["amount"], money(o.get("amount") or 0))
            c["tds"] = max(c["tds"], money(o.get("tax_deducted") or 0))
            c["sources"].add("AIS")
            c["evidence"].append(ev(o))
        declared = msum(b.gross_receipts.amount for b in case.income.business)
        total_third = msum(c["amount"] for c in per_client.values())
        if declared <= 0:
            for k, c in per_client.items():
                delta = self.safe_delta(imp.add_business_receipts(c["amount"]))
                self.emit(kind="MISSING", category="BUSINESS", key=f"{k}:{'194J'}", severity="HIGH", title=f"Professional receipts from {c['name']} not accounted for",
                          description=f"Tax of {fmt(c['tds'])} was deducted on {fmt(c['amount'])} paid by {c['name']}, but the return has no business / professional income. This item requires review.",
                          evidence=c["evidence"], impact=delta, impact_note="Estimated change in total tax if the receipts are added (44ADA presumptive income).", required_action="Confirm the receipts and add them.",
                          resolutions=[SuggestedResolution(action="ADD_INCOME", label=f"Add receipts {fmt(c['amount'])} from {c['name']}", payload={"head": "BUSINESS", "description": f"Receipts from {c['name']}", "amount": float(c["amount"]), "tds": float(c["tds"])})])
            return
        if not differs(total_third, declared) or total_third <= declared:
            return
        diff = money(total_third - declared)
        # attribute the shortfall to clients that have no certificate (Form 16A) when their receipts explain it
        uncertified = {k: c for k, c in per_client.items() if "FORM16A" not in c["sources"]}
        candidates = uncertified if uncertified and not differs(msum(c["amount"] for c in uncertified.values()), diff) else {}
        if candidates:
            for k, c in candidates.items():
                delta = self.safe_delta(imp.add_business_receipts(c["amount"]))
                self.emit(kind="MISSING", category="BUSINESS", key=f"{k}:194J", severity="HIGH", title=f"Professional receipts from {c['name']} not accounted for",
                          description=f"Form 26AS / AIS show {fmt(c['amount'])} paid by {c['name']} (TDS {fmt(c['tds'])} u/s 194J); declared receipts of {fmt(declared)} do not include it. This item requires review.",
                          evidence=c["evidence"], impact=delta, impact_note="Estimated change in total tax if the receipts are added.", required_action="Confirm the receipts from this client and add them.",
                          resolutions=[SuggestedResolution(action="ADD_INCOME", label=f"Add receipts {fmt(c['amount'])} from {c['name']}", payload={"head": "BUSINESS", "description": f"Receipts from {c['name']}", "amount": float(c["amount"]), "tds": float(c["tds"])})])
        else:
            delta = self.safe_delta(imp.add_business_receipts(diff))
            self.emit(kind="MISMATCH", category="BUSINESS", key="third-party-vs-declared", severity="HIGH", title="Professional receipts reported by payers exceed declared receipts",
                      description=f"Form 26AS / AIS report receipts of {fmt(total_third)} while the return shows {fmt(declared)} (difference {fmt(diff)}). This item requires review.",
                      evidence=[e for c in per_client.values() for e in c["evidence"]][:8], impact=delta, impact_note="Estimated change in total tax if the difference is added.", required_action="Reconcile receipts client by client.",
                      resolutions=[SuggestedResolution(action="ADD_INCOME", label=f"Add receipts {fmt(diff)}", payload={"head": "BUSINESS", "description": "Receipts per 26AS/AIS", "amount": float(diff)})])

    # ------------------------------------------------------------------ TDS ledger
    def reconcile_tds(self) -> None:
        case = self.case
        groups: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for t in case.tax_deducted:
            groups[(self.key_of(t.deductor_name, t.deductor_tan), t.section.replace("-", "").upper())][t.source_type.value].append(t)
        for (key, section), per_source in groups.items():
            name = self.display(key, next(iter(per_source.values()))[0].deductor_name)
            amounts = {src: msum(x.tax_deducted.amount for x in lst) for src, lst in per_source.items()}
            entity_ids = [x.id for lst in per_source.values() for x in lst]
            if len(set(amounts.values())) > 1:
                srcs = sorted(amounts.items(), key=lambda kv: -kv[1])
                hi, lo = srcs[0], srcs[-1]
                if differs(hi[1], lo[1]):
                    diff = money(hi[1] - lo[1])
                    evidence = [EvidenceRef(label=f"{SOURCE_LABELS[SourceType(src)]} · section {section}", source_type=SourceType(src), document_id=(lst[0].document_ids[0] if lst[0].document_ids else None),
                                            entity_id=lst[0].id, amount=amounts[src], reference=f"section {section}") for src, lst in per_source.items()]
                    self.emit(kind="MISMATCH", category="TDS", key=f"{key}:{section}", severity="HIGH" if diff >= 1000 else "MEDIUM", title=f"TDS reported by {name} differs between sources",
                              description=f"{SOURCE_LABELS[SourceType(hi[0])]} shows {fmt(hi[1])} while {SOURCE_LABELS[SourceType(lo[0])]} shows {fmt(lo[1])} (section {section}). Credit is generally limited to what Form 26AS reflects; this item requires review.",
                              evidence=evidence, entity_ids=entity_ids, impact=diff, impact_note="TDS credit at risk (difference between the sources).",
                              required_action="Check with the deductor whether the full amount was deposited and the TDS return filed; use the 26AS figure until it is corrected.",
                              resolutions=[SuggestedResolution(action="CONFIRM_ENTITY", label="Use Form 26AS figure", payload={"prefer_source": "FORM26AS", "key": key, "section": section}),
                                           SuggestedResolution(action="REVIEW", label="Mark as reviewed – deductor will correct", payload={})])
            if section in ("192", "192A") or section.startswith("194A") or section in ("194", "194K", "194J", "194JA", "194JB", "194H", "194C", "194I", "194IB", "194IA"):
                continue  # income-side checks cover these sections
            first = next(iter(per_source.values()))[0]
            amount_paid = money(first.amount_paid_credited.amount)
            if amount_paid <= 0 or self.covered(key, "INTEREST", "DIVIDEND", "RENTAL", "BUSINESS", "SALARY", "OTHER"):
                continue
            self.emit(kind="MISSING", category="OTHER", key=f"{key}:{section}", severity="MEDIUM", title=f"Income from {name} (section {section}) not accounted for",
                      description=f"Tax of {fmt(first.tax_deducted.amount)} was deducted u/s {section} on {fmt(amount_paid)} paid by {name}, but the return contains no matching income. This item requires review.",
                      evidence=[ev_tds(t) for lst in per_source.values() for t in lst], entity_ids=entity_ids, required_action="Confirm the nature of this income and add it under the correct head.",
                      resolutions=[SuggestedResolution(action="ADD_INCOME", label=f"Add income {fmt(amount_paid)} from {name}", payload={"head": "OTHER", "description": f"Income from {name} (s.{section})", "amount": float(amount_paid), "tds": float(first.tax_deducted.amount)})])

    # ------------------------------------------------------------------ TIS totals
    def reconcile_tis(self) -> None:
        case = self.case
        tis = {o.get("tis_category", ""): money(o.get("amount") or 0) for o in self.by_category("TIS_SUMMARY")}
        if not tis:
            return
        declared = {
            "Salary": msum(s.gross_salary.amount for s in case.income.salary),
            "Dividend": msum(d.amount.amount for d in case.income.dividend),
            "Interest from savings bank": msum(i.amount.amount for i in case.income.interest if i.kind == "SAVINGS"),
            "Interest from deposit": msum(i.amount.amount for i in case.income.interest if i.kind in ("FIXED_DEPOSIT", "RECURRING_DEPOSIT")),
        }
        cat_map = {"Salary": "SALARY", "Dividend": "DIVIDEND", "Interest from savings bank": "INTEREST", "Interest from deposit": "INTEREST"}
        for cat, amount in tis.items():
            if cat not in declared or amount <= 0 or not differs(amount, declared[cat]):
                continue
            if any(i.category == cat_map[cat] for i in self.items):
                continue  # detailed items already explain the difference
            self.emit(kind="MISMATCH", category="OTHER", key=f"tis:{cat}", severity="INFO", title=f"TIS total for '{cat}' differs from the return",
                      description=f"TIS derived value {fmt(amount)} vs return {fmt(declared[cat])}. Information only – review the category in the AIS/TIS portal.",
                      evidence=[ev(o) for o in self.by_category("TIS_SUMMARY") if o.get("tis_category") == cat], required_action="No action if the detailed items are already resolved.")

    # ------------------------------------------------------------------ previous year
    def reconcile_previous_year(self) -> None:
        case = self.case
        prev = case.previous_return
        if prev is None:
            return
        current = {
            "Salary": msum(s.gross_salary.amount for s in case.income.salary),
            "House property": msum(r.annual_rent_received.amount for r in case.income.rental),
            "Capital gains": msum(t.sale_consideration.amount - t.cost_of_acquisition.amount for t in case.income.capital_gains),
            "Other sources": msum(i.amount.amount for i in case.income.interest) + msum(d.amount.amount for d in case.income.dividend),
            "Business": msum(b.gross_receipts.amount for b in case.income.business),
        }
        notes = []
        for head, prev_amt in prev.income_heads.items():
            cur = current.get(head)
            if cur is None:
                continue
            prev_amt = money(prev_amt)
            if prev_amt > 0 and cur <= 0:
                notes.append(f"{head}: {fmt(prev_amt)} last year, nothing this year")
            elif prev_amt > 0 and cur > 0 and abs(cur - prev_amt) > max(Decimal(50000), prev_amt * Decimal("0.5")):
                notes.append(f"{head}: {fmt(prev_amt)} last year vs {fmt(cur)} this year")
        if notes:
            self.emit(kind="MISMATCH", category="PREVIOUS_YEAR", key=prev.assessment_year, severity="INFO", title="Previous-year information differs from the current-year profile",
                      description="Compared with your AY " + prev.assessment_year + " return: " + "; ".join(notes) + ". Information only – confirm that the change is expected.",
                      evidence=[EvidenceRef(label=f"Previous ITR · AY {prev.assessment_year}", source_type=SourceType.PREVIOUS_ITR, document_id=prev.document_id, amount=prev.gross_total_income)],
                      required_action="Confirm that income sources reported last year are complete this year.", resolutions=[SuggestedResolution(action="REVIEW", label="Acknowledge", payload={})])

    # ------------------------------------------------------------------ profile / documents
    def reconcile_profile(self) -> None:
        case = self.case
        tp = case.taxpayer
        if tp.employer_count and len(case.income.salary) < tp.employer_count and not any(i.category == "SALARY" and i.kind == "MISSING" for i in self.items):
            self.emit(kind="MISSING", category="DOCUMENT", key="form16-count", severity="MEDIUM", title=f"Form 16 available for {len(case.income.salary)} of {tp.employer_count} employers",
                      description="The profile lists more employers than the salary entries in the return. This item requires review.", evidence=[], required_action="Upload the remaining Form 16(s) or update the employer count.",
                      resolutions=[SuggestedResolution(action="UPLOAD_DOCUMENT", label="Upload Form 16", payload={"type": "FORM16"}), SuggestedResolution(action="UPDATE_PROFILE", label="Update employer count", payload={"field": "employer_count"})])
        if "CAPITAL_GAINS" in tp.income_sources and not case.income.capital_gains and not any(i.category == "CAPITAL_GAINS" for i in self.items):
            self.emit(kind="MISSING", category="CAPITAL_GAINS", key="profile", severity="MEDIUM", title="Capital gains selected in the profile but no transactions recorded",
                      description="You indicated capital-gains activity during onboarding; no broker or capital-gains statement has been processed yet.", evidence=[], required_action="Upload the broker tax P&L / capital-gains statement.",
                      resolutions=[SuggestedResolution(action="UPLOAD_DOCUMENT", label="Upload broker statement", payload={"type": "BROKER_STATEMENT"})])
        if (tp.has_rental_income or "RENTAL" in tp.income_sources) and not any(not r.is_self_occupied for r in case.income.rental) and not any(i.category == "RENTAL" for i in self.items):
            self.emit(kind="MISSING", category="RENTAL", key="profile", severity="MEDIUM", title="Rental income selected in the profile but no let-out property recorded",
                      description="You indicated rental income during onboarding; no let-out property exists in the return.", evidence=[], required_action="Add the property and annual rent.",
                      resolutions=[SuggestedResolution(action="ADD_INCOME", label="Add rental property", payload={"head": "RENTAL"})])
        if ("SALARY" in tp.income_sources or tp.employment_status in ("SALARIED", "BOTH")) and not case.income.salary and not any(i.category == "SALARY" for i in self.items):
            self.emit(kind="MISSING", category="SALARY", key="profile", severity="HIGH", title="Salary income selected but no Form 16 processed",
                      description="No salary entry exists yet. Upload Form 16 or enter salary details.", evidence=[], required_action="Upload Form 16.",
                      resolutions=[SuggestedResolution(action="UPLOAD_DOCUMENT", label="Upload Form 16", payload={"type": "FORM16"})])
        for s in case.income.salary:
            if s.period_from and s.period_to and tp.employer_count == 1 and (s.period_from > self.fy_start or s.period_to < self.fy_end):
                self.emit(kind="TIMING", category="SALARY", key=f"{s.id}:period", severity="LOW", title=f"Form 16 from {s.employer_name} covers only part of the year",
                          description=f"The certificate covers {s.period_from.isoformat()} to {s.period_to.isoformat()}. If you were employed elsewhere for the rest of the year, that income must be added.",
                          evidence=[ev_entity(s, "Employment period", s.gross_salary.amount, s.gross_salary)], entity_ids=[s.id], required_action="Confirm there was no other employer during the year.",
                          resolutions=[SuggestedResolution(action="REVIEW", label="No other employer", payload={})])

    # ------------------------------------------------------------------ run
    def run(self) -> list[ReconciliationItem]:
        self.reconcile_salary()
        self.reconcile_interest()
        self.reconcile_dividends()
        self.reconcile_capital_gains()
        self.reconcile_rent()
        self.reconcile_business()
        self.reconcile_tds()
        self.reconcile_tis()
        self.reconcile_previous_year()
        self.reconcile_profile()
        return self.items


def reconcile(case: TaxCase) -> list[ReconciliationItem]:
    """Run reconciliation and merge with existing items, preserving RESOLVED / DISMISSED statuses."""
    new_items = Reconciler(case).run()
    previous = {i.fingerprint.split("#")[0]: i for i in case.reconciliation_items}
    merged: list[ReconciliationItem] = []
    for item in new_items:
        base = item.fingerprint.split("#")[0]
        old = previous.get(base)
        if old is not None:
            item.id = old.id
            item.created_at = old.created_at
            if old.status in ("RESOLVED", "DISMISSED"):
                item.status = old.status
                item.resolution_note = old.resolution_note
                item.resolved_at = old.resolved_at
        merged.append(item)
    emitted = {i.fingerprint.split("#")[0] for i in merged}
    for base, old in previous.items():
        if base not in emitted and old.status in ("RESOLVED", "DISMISSED"):
            merged.append(old)  # keep the resolution history even though the underlying condition is gone
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
    merged.sort(key=lambda i: (0 if i.status == "OPEN" else 1, order.get(i.severity, 9), i.category))
    case.reconciliation_items = merged
    case.meta.last_reconciliation_at = utcnow()
    return merged
