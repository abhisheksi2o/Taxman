"""Extractors for CSV statements: bank statements, broker / capital-gains statements, dividend statements.

pandas is used for tolerant CSV parsing (header normalisation, numeric coercion)."""
from __future__ import annotations

import io
import re
from datetime import date

import pandas as pd

from .common import ExtractionResult, mask_account, normalize_key, obs, parse_amount, parse_date

INTEREST_PATTERNS = re.compile(r"\b(INT\.?PD|INTEREST|INT CREDIT|SB INT|FD INT|RD INT|TD INT|INT ON)\b", re.I)
SALARY_PATTERNS = re.compile(r"\b(SALARY|SAL CREDIT|PAYROLL)\b", re.I)
RENT_PATTERNS = re.compile(r"\b(RENT)\b", re.I)
DIVIDEND_PATTERNS = re.compile(r"\b(DIV|DIVIDEND)\b", re.I)
REFUND_PATTERNS = re.compile(r"\b(ITR REFUND|INCOME TAX REFUND|IT REFUND|ITREF)\b", re.I)


def _read_csv(data: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(data), dtype=str, keep_default_na=False)
    df.columns = [re.sub(r"[^a-z0-9]+", "_", c.strip().lower()).strip("_") for c in df.columns]
    return df


def _num(v: str) -> float:
    d = parse_amount(v)
    return float(d) if d is not None else 0.0


def _col(df: pd.DataFrame, *names: str) -> str | None:
    for n in names:
        if n in df.columns:
            return n
    return None


# --------------------------------------------------------------------------- Bank statement


def extract_bank_statement(data: str, fy_start: date | None = None, fy_end: date | None = None) -> ExtractionResult:
    r = ExtractionResult()
    f = r.fields
    header_lines = []
    lines = data.splitlines()
    start = 0
    for i, line in enumerate(lines[:15]):
        low = line.lower()
        if "date" in low and ("narration" in low or "description" in low or "particulars" in low):
            start = i
            break
        header_lines.append(line)
    meta = "\n".join(header_lines)
    f["bank_name"] = next((l.split(",")[0].strip() for l in header_lines if l.strip()), None)
    acct = re.search(r"Account (?:No|Number)[^:]*:\s*([0-9X]+)", meta, re.I)
    f["account_ref"] = mask_account(acct.group(1)) if acct else None
    f["account_type"] = "SAVINGS" if re.search(r"savings", meta, re.I) else ("CURRENT" if re.search(r"current", meta, re.I) else "SAVINGS")
    df = _read_csv("\n".join(lines[start:]))
    dcol = _col(df, "date", "txn_date", "transaction_date", "value_date")
    ncol = _col(df, "narration", "description", "particulars", "details")
    rcol = _col(df, "ref_no", "reference", "chq_ref_no", "cheque_ref_no", "ref")
    ccol = _col(df, "credit", "deposit", "deposit_amt", "credit_amount")
    dbcol = _col(df, "debit", "withdrawal", "withdrawal_amt", "debit_amount")
    bcol = _col(df, "balance", "closing_balance")
    if not (dcol and ncol and ccol):
        r.flags.append("Could not identify date / narration / credit columns.")
        r.score(0, 1)
        return r
    txns = []
    interest_total = 0.0
    seen: dict[tuple, int] = {}
    duplicates = []
    out_of_period = 0
    for idx, row in df.iterrows():
        d = parse_date(row[dcol])
        narration = str(row[ncol]).strip()
        credit = _num(row[ccol]) if ccol else 0.0
        debit = _num(row[dbcol]) if dbcol else 0.0
        ref = str(row[rcol]).strip() if rcol else f"row{idx + 1}"
        kind = "OTHER"
        if credit > 0:
            if INTEREST_PATTERNS.search(narration):
                kind = "INTEREST"
            elif SALARY_PATTERNS.search(narration):
                kind = "SALARY"
            elif RENT_PATTERNS.search(narration):
                kind = "RENT"
            elif DIVIDEND_PATTERNS.search(narration):
                kind = "DIVIDEND"
            elif REFUND_PATTERNS.search(narration):
                kind = "IT_REFUND"
        sub_kind = None
        if kind == "INTEREST":
            up = narration.upper()
            sub_kind = "FIXED_DEPOSIT" if ("FD" in up or "TD" in up or "DEPOSIT" in up) else ("RECURRING_DEPOSIT" if "RD" in up.split() else "SAVINGS")
        t = {"row": int(idx) + 1, "date": d.isoformat() if d else str(row[dcol]), "narration": narration, "ref": ref, "credit": credit, "debit": debit,
             "balance": _num(row[bcol]) if bcol else None, "kind": kind, "sub_kind": sub_kind}
        key = (t["date"], narration, credit, debit)
        if key in seen and credit > 0:
            duplicates.append({"row": t["row"], "duplicate_of_row": seen[key], "amount": credit, "narration": narration, "date": t["date"], "ref": ref})
        else:
            seen[key] = t["row"]
        if fy_start and fy_end and d and not (fy_start <= d <= fy_end):
            out_of_period += 1
            t["out_of_period"] = True
        txns.append(t)
        if kind == "INTEREST":
            interest_total += credit
    f["transaction_count"] = len(txns)
    f["interest_credits_total"] = round(interest_total, 2)
    f["interest_credit_count"] = sum(1 for t in txns if t["kind"] == "INTEREST")
    f["salary_credits_total"] = round(sum(t["credit"] for t in txns if t["kind"] == "SALARY"), 2)
    f["rent_credits_total"] = round(sum(t["credit"] for t in txns if t["kind"] == "RENT"), 2)
    f["dividend_credits_total"] = round(sum(t["credit"] for t in txns if t["kind"] == "DIVIDEND"), 2)
    f["duplicates"] = duplicates
    f["out_of_period_rows"] = out_of_period
    f["period_from"] = min((t["date"] for t in txns if t.get("date")), default=None)
    f["period_to"] = max((t["date"] for t in txns if t.get("date")), default=None)
    if duplicates:
        r.flags.append(f"{len(duplicates)} transaction(s) appear twice with identical date, narration and amount.")
    if out_of_period:
        r.flags.append(f"{out_of_period} transaction(s) fall outside the financial year.")
    r.score(3, 3, penalty=0.0)
    r.period_label = f"{f['period_from']} → {f['period_to']}" if f["period_from"] else None
    key = normalize_key(f["bank_name"])
    for t in txns:
        if t["kind"] in ("INTEREST", "RENT", "DIVIDEND", "IT_REFUND", "SALARY"):
            r.observations.append(obs("BANK_CREDIT", kind=t["kind"], sub_kind=t["sub_kind"], counterparty=f["bank_name"], counterparty_key=key, account_ref=f["account_ref"],
                                      amount=t["credit"], date=t["date"], narration=t["narration"], reference=f"Transaction #{t['ref']}", row=t["row"],
                                      out_of_period=bool(t.get("out_of_period")), confidence=0.9 if t["kind"] == "INTEREST" else 0.7))
    for dmp in duplicates:
        r.observations.append(obs("BANK_DUPLICATE", counterparty=f["bank_name"], counterparty_key=key, account_ref=f["account_ref"], amount=dmp["amount"], date=dmp["date"],
                                  narration=dmp["narration"], reference=f"Transaction #{dmp['ref']} (row {dmp['row']} duplicates row {dmp['duplicate_of_row']})"))
    # keep only a compact transaction table for the UI (never the full statement)
    f["sample_transactions"] = [t for t in txns if t["kind"] != "OTHER"][:40]
    return r


# --------------------------------------------------------------------------- Broker / capital gains statement


def extract_broker_statement(data: str) -> ExtractionResult:
    r = ExtractionResult()
    f = r.fields
    lines = data.splitlines()
    start = next((i for i, l in enumerate(lines[:10]) if ("buy date" in l.lower() or "sell date" in l.lower()) and "," in l), 0)
    f["broker_name"] = next((l.split(",")[0].strip() for l in lines[:start] if l.strip()), None) if start else None
    df = _read_csv("\n".join(lines[start:]))
    sym = _col(df, "symbol", "scrip", "security", "scheme")
    isin = _col(df, "isin")
    atype = _col(df, "asset_type", "type", "segment")
    qty = _col(df, "quantity", "qty", "units")
    bdate = _col(df, "buy_date", "purchase_date", "acquisition_date")
    bval = _col(df, "buy_value", "purchase_value", "cost")
    sdate = _col(df, "sell_date", "sale_date", "transfer_date")
    sval = _col(df, "sell_value", "sale_value", "consideration")
    charges = _col(df, "charges", "expenses", "brokerage")
    ref = _col(df, "trade_ref", "ref", "contract_note")
    if not (sym and bdate and sdate and sval and bval):
        r.flags.append("Could not identify the trade columns (symbol, buy/sell dates, values).")
        r.score(0, 1)
        return r
    trades = []
    for idx, row in df.iterrows():
        t_raw = str(row[atype]).upper() if atype else "EQ"
        asset_class = "LISTED_EQUITY"
        if "MF" in t_raw or "FUND" in t_raw:
            asset_class = "DEBT_MF" if "DEBT" in t_raw else "EQUITY_MF"
        elif "BOND" in t_raw:
            asset_class = "LISTED_BOND"
        elif "GOLD" in t_raw:
            asset_class = "GOLD"
        bd, sd = parse_date(row[bdate]), parse_date(row[sdate])
        trades.append({"symbol": str(row[sym]).strip(), "isin": str(row[isin]).strip() if isin else None, "asset_class": asset_class, "raw_type": t_raw,
                       "quantity": _num(row[qty]) if qty else None, "buy_date": bd.isoformat() if bd else None, "buy_value": _num(row[bval]),
                       "sell_date": sd.isoformat() if sd else None, "sell_value": _num(row[sval]), "charges": _num(row[charges]) if charges else 0.0,
                       "ref": str(row[ref]).strip() if ref else f"row{idx + 1}"})
        if sd is None:
            r.flags.append(f"Row {idx + 1}: sale date could not be parsed.")
        if bd is None:
            r.flags.append(f"Row {idx + 1}: acquisition date missing – holding period cannot be determined.")
    f["trades"] = trades
    f["total_sale_value"] = round(sum(t["sell_value"] for t in trades), 2)
    f["total_buy_value"] = round(sum(t["buy_value"] for t in trades), 2)
    f["trade_count"] = len(trades)
    r.score(len(trades) and 3 or 0, 3, penalty=0.03 * len(r.flags))
    key = normalize_key(f["broker_name"])
    for t in trades:
        r.observations.append(obs("CAPITAL_GAIN_TXN", counterparty=f["broker_name"], counterparty_key=key, symbol=t["symbol"], isin=t["isin"], asset_class=t["asset_class"], raw_type=t["raw_type"],
                                  quantity=t["quantity"], acquisition_date=t["buy_date"], cost=t["buy_value"], date=t["sell_date"], amount=t["sell_value"], charges=t["charges"],
                                  reference=f"Trade {t['ref']}", confidence=r.confidence))
    return r


# --------------------------------------------------------------------------- Dividend statement


def extract_dividend_statement(data: str) -> ExtractionResult:
    r = ExtractionResult()
    f = r.fields
    lines = data.splitlines()
    start = next((i for i, l in enumerate(lines[:10]) if ("symbol" in l.lower() or "isin" in l.lower() or "company" in l.lower()) and "," in l), 0)
    f["source_name"] = next((l.split(",")[0].strip() for l in lines[:start] if l.strip()), None) if start else None
    df = _read_csv("\n".join(lines[start:]))
    sym = _col(df, "symbol", "company", "scrip")
    isin = _col(df, "isin")
    pdate = _col(df, "payment_date", "date", "ex_date")
    gross = _col(df, "gross_dividend", "dividend_amount", "amount")
    tds = _col(df, "tds", "tax_deducted")
    if not (sym and gross):
        r.flags.append("Could not identify dividend columns.")
        r.score(0, 1)
        return r
    rows = []
    for idx, row in df.iterrows():
        d = parse_date(row[pdate]) if pdate else None
        rows.append({"company": str(row[sym]).strip(), "isin": str(row[isin]).strip() if isin else None, "date": d.isoformat() if d else None,
                     "gross": _num(row[gross]), "tds": _num(row[tds]) if tds else 0.0})
    f["rows"] = rows
    f["total_dividend"] = round(sum(x["gross"] for x in rows), 2)
    f["total_tds"] = round(sum(x["tds"] for x in rows), 2)
    r.score(1 if rows else 0, 1)
    for x in rows:
        r.observations.append(obs("DIVIDEND", counterparty=x["company"], counterparty_key=normalize_key(x["company"]), isin=x["isin"], amount=x["gross"], tax_deducted=x["tds"],
                                  date=x["date"], reference=f"Dividend · {x['company']} · {x['date']}", confidence=r.confidence))
    return r
