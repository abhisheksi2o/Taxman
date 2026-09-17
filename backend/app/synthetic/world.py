"""Synthetic "world" for demo taxpayers – coherent financial relationships, not random numbers.

Salary → Form 16 → TDS → 26AS/AIS · Bank → interest → certificate/AIS · Broker → trades → gains/AIS …
Every downstream document is rendered from this single world so the sources agree unless a scenario
deliberately injects a hidden issue.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.models.common import money
from app.tax_engine.rules import get_rules
from app.tax_engine.rules.base import AYRules

FIRST_NAMES = ["Priya", "Arjun", "Meera", "Rahul", "Ananya", "Vikram", "Kavya", "Rohan", "Sneha", "Aditya", "Nisha", "Karthik"]
LAST_NAMES = ["Raghavan", "Mehta", "Iyer", "Sharma", "Nair", "Kulkarni", "Banerjee", "Reddy", "Desai", "Chawla", "Menon", "Bose"]
CITIES = [("Bengaluru", "METRO", "Karnataka"), ("Mumbai", "METRO", "Maharashtra"), ("Pune", "NON_METRO", "Maharashtra"), ("Hyderabad", "NON_METRO", "Telangana"), ("Chennai", "METRO", "Tamil Nadu"), ("Delhi", "METRO", "Delhi")]
EMPLOYERS = [("Nimbus Software Private Limited", "BLRN"), ("Harbor Analytics India LLP", "MUMH"), ("Sundial Fintech Private Limited", "PNES"), ("Kestrel Health Systems Limited", "HYDK"), ("Aurora Retail Ventures Private Limited", "CHEA"), ("Vantage Logistics India Private Limited", "DELV")]
BANKS = [("Meridian Bank Limited", "MUMM"), ("Suryodaya Co-operative Bank", "PNES"), ("Cascade National Bank", "DELC"), ("Lotus Federal Bank", "BLRL")]
CLIENTS = [("Orbit Media Works LLP", "MUMO"), ("Greenfield Consulting Private Limited", "BLRG"), ("Skyline Edutech Limited", "DELS"), ("Pixel Forge Studios", "PNEP")]
STOCKS = [("INFY", "INE009A01021", "Infosys Limited", 1450), ("TCS", "INE467B01029", "Tata Consultancy Services", 3600), ("HDFCBANK", "INE040A01034", "HDFC Bank Limited", 1650), ("RELIANCE", "INE002A01018", "Reliance Industries", 2450), ("ITC", "INE154A01025", "ITC Limited", 430), ("LT", "INE018A01030", "Larsen & Toubro", 3400)]
FUNDS = [("Bluechip Equity Fund - Growth", "INF209K01ABC", "EQUITY MF", 92.0), ("Short Duration Debt Fund - Growth", "INF209K01XYZ", "DEBT MF", 28.0), ("Flexi Cap Fund - Growth", "INF846K01DEF", "MF", 61.0)]
LENDERS = ["Harbor Housing Finance Limited", "Meridian Bank Limited"]


def pan_for(rng: random.Random, surname: str) -> str:
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "".join(rng.choice(letters) for _ in range(3)) + "P" + surname[0].upper() + f"{rng.randint(1000, 9999)}" + rng.choice(letters)


def tan_for(rng: random.Random, prefix: str) -> str:
    return prefix + f"{rng.randint(10000, 99999)}" + rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ")


@dataclass
class Person:
    name: str
    pan: str
    dob: date
    city: str
    city_type: str
    state: str
    address: str
    email: str
    phone: str


@dataclass
class Employer:
    name: str
    tan: str
    pan: str
    address: str
    from_month: int  # 0 = April … 11 = March
    to_month: int
    basic_monthly: Decimal
    hra_monthly: Decimal
    special_monthly: Decimal
    pf_monthly: Decimal
    pt_monthly: Decimal
    nps_employer_monthly: Decimal = Decimal("0")
    regime_with_employer: str = "NEW"
    tds_total: Decimal = Decimal("0")
    tds_quarters: list[Decimal] = field(default_factory=list)
    hra_exempt: Decimal = Decimal("0")

    @property
    def months(self) -> int:
        return self.to_month - self.from_month + 1

    annual_basic: Decimal = Decimal("0")
    annual_hra: Decimal = Decimal("0")
    annual_special: Decimal = Decimal("0")

    @property
    def gross_annual(self) -> Decimal:
        return money(self.annual_basic + self.annual_hra + self.annual_special)

    @property
    def basic_annual(self) -> Decimal:
        return money(self.annual_basic)

    @property
    def hra_annual(self) -> Decimal:
        return money(self.annual_hra)

    @property
    def pt_annual(self) -> Decimal:
        return money(self.pt_monthly * self.months)

    @property
    def nps_annual(self) -> Decimal:
        return (self.annual_basic * Decimal("0.10")).quantize(Decimal("1")) if self.nps_employer_monthly > 0 else Decimal("0")


@dataclass
class FixedDeposit:
    number: str
    principal: Decimal
    rate: Decimal
    interest: Decimal
    tds: Decimal
    credit_dates: list[date]


@dataclass
class BankAccountW:
    bank: str
    tan: str
    number: str
    account_type: str = "SAVINGS"
    is_primary: bool = False
    savings_interest: list[tuple[date, Decimal]] = field(default_factory=list)
    fds: list[FixedDeposit] = field(default_factory=list)
    opening_balance: Decimal = Decimal("150000")

    @property
    def savings_total(self) -> Decimal:
        return money(sum((a for _, a in self.savings_interest), Decimal("0")))

    @property
    def fd_interest_total(self) -> Decimal:
        return money(sum((f.interest for f in self.fds), Decimal("0")))

    @property
    def fd_tds_total(self) -> Decimal:
        return money(sum((f.tds for f in self.fds), Decimal("0")))


@dataclass
class Trade:
    symbol: str
    isin: str
    asset_type: str  # EQ / EQUITY MF / DEBT MF / MF
    quantity: Decimal
    buy_date: date
    buy_value: Decimal
    sell_date: date
    sell_value: Decimal
    charges: Decimal
    ref: str


@dataclass
class DividendPayout:
    company: str
    isin: str
    paid_on: date
    quantity: Decimal
    dps: Decimal
    gross: Decimal
    tds: Decimal


@dataclass
class PropertyW:
    name: str
    address: str
    tenant: str
    tenant_pan: str
    monthly_rent: Decimal
    months: int
    municipal_tax: Decimal
    tenant_tds_total: Decimal = Decimal("0")
    tenant_tan: str | None = None


@dataclass
class HomeLoanW:
    lender: str
    account: str
    interest: Decimal
    principal: Decimal
    sanction_date: date
    self_occupied_address: str


@dataclass
class ClientW:
    name: str
    tan: str
    receipts_quarters: list[Decimal]
    tds_rate: Decimal = Decimal("0.10")

    @property
    def total(self) -> Decimal:
        return money(sum(self.receipts_quarters, Decimal("0")))

    @property
    def tds_total(self) -> Decimal:
        return money(self.total * self.tds_rate)


@dataclass
class InvestmentW:
    instrument: str
    provider: str
    amount: Decimal
    date: date
    section: str


@dataclass
class World:
    rules: AYRules
    person: Person
    employers: list[Employer] = field(default_factory=list)
    banks: list[BankAccountW] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    dividends: list[DividendPayout] = field(default_factory=list)
    properties: list[PropertyW] = field(default_factory=list)
    loans: list[HomeLoanW] = field(default_factory=list)
    clients: list[ClientW] = field(default_factory=list)
    investments: list[InvestmentW] = field(default_factory=list)
    prev_itr: dict | None = None
    advance_tax: list[tuple[date, Decimal, str]] = field(default_factory=list)

    @property
    def ay(self) -> str:
        return self.rules.assessment_year

    @property
    def fy(self) -> str:
        return self.rules.financial_year


def month_date(rules: AYRules, month_index: int, day: int = 1) -> date:
    """month_index 0 = April of the FY."""
    y = rules.fy_start.year + (1 if month_index >= 9 else 0)
    m = (3 + month_index) % 12 + 1
    return date(y, m, min(day, 28))


def month_end(rules: AYRules, month_index: int) -> date:
    nxt = month_date(rules, month_index + 1) if month_index < 11 else date(rules.fy_end.year, 4, 1)
    return nxt - timedelta(days=1)


def month_label(rules: AYRules, month_index: int) -> str:
    return month_date(rules, month_index).strftime("%B %Y")


QUARTER_ENDS = [(2, 30), (5, 30), (8, 31), (11, 31)]  # month index, day


def quarter_end(rules: AYRules, q: int) -> date:
    mi, _ = QUARTER_ENDS[q]
    return month_end(rules, mi)


class WorldBuilder:
    def __init__(self, seed: int, assessment_year: str):
        self.rng = random.Random(seed)
        self.rules = get_rules(assessment_year)
        self.seed = seed
        self._counter = 0

    def _ref(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}{self.seed % 1000:03d}{self._counter:04d}"

    def person(self, age: int | None = None) -> Person:
        rng = self.rng
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        city, ctype, state = rng.choice(CITIES)
        age = age or rng.randint(27, 45)
        dob = date(self.rules.fy_end.year - age, rng.randint(1, 12), rng.randint(1, 28))
        return Person(name=f"{first} {last}", pan=pan_for(rng, last), dob=dob, city=city, city_type=ctype, state=state,
                      address=f"{rng.randint(4, 220)}, {rng.choice(['Lakeview Residency', 'Palm Grove Apartments', 'Cedar Heights', 'Maple Enclave'])}, {city} {rng.randint(400001, 600099)}",
                      email=f"{first.lower()}.{last.lower()}@example.com", phone=f"9{rng.randint(100000000, 999999999)}")

    def employer(self, annual_ctc: int, from_month: int = 0, to_month: int = 11, regime: str = "NEW", nps: bool = False, idx: int | None = None) -> Employer:
        rng = self.rng
        name, prefix = EMPLOYERS[idx if idx is not None else rng.randrange(len(EMPLOYERS))]
        months = to_month - from_month + 1
        # whole-rupee annual figures so that every downstream document agrees to the rupee
        paid = (Decimal(annual_ctc) * months / 12).quantize(Decimal("1"))
        annual_basic = (paid * Decimal("0.5")).quantize(Decimal("1"))
        annual_hra = (annual_basic * Decimal("0.4")).quantize(Decimal("1"))
        annual_special = paid - annual_basic - annual_hra
        basic = money(annual_basic / months)
        hra = money(annual_hra / months)
        special = money(annual_special / months)
        pf = money(basic * Decimal("0.12"))
        pt = Decimal("200")
        emp = Employer(name=name, tan=tan_for(rng, prefix), pan=f"AAAC{name[0]}{rng.randint(1000, 9999)}{rng.choice('ABCDEFGH')}",
                       address=f"{rng.randint(1, 99)} {rng.choice(['Outer Ring Road', 'Marine Drive', 'Hinjewadi Phase 2', 'HITEC City', 'OMR', 'Cyber Hub'])}",
                       from_month=from_month, to_month=to_month, basic_monthly=basic, hra_monthly=hra, special_monthly=special, pf_monthly=pf, pt_monthly=pt,
                       nps_employer_monthly=(money(basic * Decimal("0.10")) if nps else Decimal("0")), regime_with_employer=regime,
                       annual_basic=annual_basic, annual_hra=annual_hra, annual_special=annual_special)
        return emp

    def bank(self, idx: int, primary: bool = False, savings_annual: int = 0, fds: list[tuple[int, str]] | None = None) -> BankAccountW:
        rng = self.rng
        name, prefix = BANKS[idx % len(BANKS)]
        acct = BankAccountW(bank=name, tan=tan_for(rng, prefix), number=f"{rng.randint(10, 99)}{rng.randint(10000000, 99999999)}", is_primary=primary)
        if savings_annual:
            q = [money(Decimal(savings_annual) * Decimal(w)) for w in ("0.22", "0.24", "0.26", "0.28")]
            q[3] = money(Decimal(savings_annual) - sum(q[:3], Decimal("0")))
            acct.savings_interest = [(quarter_end(self.rules, i), q[i]) for i in range(4)]
        for principal, rate in (fds or []):
            r = Decimal(rate)
            interest = money(Decimal(principal) * r)
            thr = self.rules.tds_thresholds["194A_BANK"].threshold
            tds = money(interest * Decimal("0.10")) if interest > thr else Decimal("0")
            acct.fds.append(FixedDeposit(number=f"FD{rng.randint(100000, 999999)}", principal=Decimal(principal), rate=r, interest=interest, tds=tds,
                                         credit_dates=[quarter_end(self.rules, i) for i in range(4)]))
        return acct

    def trades(self, n_equity: int = 3, long_term: int = 1, funds: list[int] | None = None) -> list[Trade]:
        rng = self.rng
        out = []
        picks = rng.sample(STOCKS, k=min(n_equity, len(STOCKS)))
        for i, (sym, isin, _, price) in enumerate(picks):
            qty = Decimal(rng.choice([10, 15, 20, 25, 40, 50]))
            sell = month_date(self.rules, rng.randint(2, 10), rng.randint(2, 26))
            if i < long_term:
                buy = sell - timedelta(days=rng.randint(400, 900))
                buy_price = Decimal(price) * Decimal(str(round(rng.uniform(0.55, 0.8), 3)))
            else:
                buy = sell - timedelta(days=rng.randint(40, 300))
                buy_price = Decimal(price) * Decimal(str(round(rng.uniform(0.85, 1.05), 3)))
            sell_price = Decimal(price) * Decimal(str(round(rng.uniform(0.95, 1.12), 3)))
            out.append(Trade(symbol=sym, isin=isin, asset_type="EQ", quantity=qty, buy_date=buy, buy_value=money(qty * buy_price), sell_date=sell,
                             sell_value=money(qty * sell_price), charges=money(qty * sell_price * Decimal("0.0012")), ref=self._ref("TR")))
        for fi in (funds or []):
            name, isin, atype, nav = FUNDS[fi]
            units = Decimal(rng.choice([500, 800, 1200]))
            sell = month_date(self.rules, rng.randint(4, 11), rng.randint(2, 26))
            buy = sell - timedelta(days=rng.randint(330, 360))  # deliberately close to the 12-month boundary
            out.append(Trade(symbol=name, isin=isin, asset_type=atype, quantity=units, buy_date=buy, buy_value=money(units * Decimal(str(nav)) * Decimal("0.9")),
                             sell_date=sell, sell_value=money(units * Decimal(str(nav))), charges=Decimal("0"), ref=self._ref("MF")))
        return out

    def dividends(self, symbols: list[tuple[str, str, str, int]] | None = None, n: int = 3) -> list[DividendPayout]:
        rng = self.rng
        out = []
        picks = symbols or rng.sample(STOCKS, k=min(n, len(STOCKS)))
        thr = self.rules.tds_thresholds["194"].threshold
        for sym, isin, company, price in picks:
            qty = Decimal(rng.choice([50, 100, 150, 200]))
            dps = Decimal(rng.choice([8, 12, 18, 22, 35]))
            gross = money(qty * dps)
            tds = money(gross * Decimal("0.10")) if gross > thr else Decimal("0")
            out.append(DividendPayout(company=company, isin=isin, paid_on=month_date(self.rules, rng.randint(3, 8), rng.randint(2, 26)), quantity=qty, dps=dps, gross=gross, tds=tds))
        return out

    def property(self, monthly_rent: int, months: int = 12, with_tds: bool = False) -> PropertyW:
        rng = self.rng
        tenant_first, tenant_last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        p = PropertyW(name="2BHK flat – Green Meadows", address=f"Flat {rng.randint(101, 909)}, Green Meadows, {self.rng.choice(CITIES)[0]}", tenant=f"{tenant_first} {tenant_last}",
                      tenant_pan=pan_for(rng, tenant_last), monthly_rent=Decimal(monthly_rent), months=months, municipal_tax=Decimal(rng.choice([6000, 8500, 12000])))
        if with_tds:
            rate = self.rules.tds_thresholds["194IB"].rate
            p.tenant_tds_total = money(p.monthly_rent * months * rate)
            p.tenant_tan = None  # individuals deduct u/s 194-IB using PAN
        return p

    def home_loan(self, interest: int, principal: int) -> HomeLoanW:
        rng = self.rng
        return HomeLoanW(lender=rng.choice(LENDERS), account=f"HL{rng.randint(10000000, 99999999)}", interest=Decimal(interest), principal=Decimal(principal),
                         sanction_date=date(self.rules.fy_start.year - rng.randint(2, 6), rng.randint(1, 12), rng.randint(1, 28)), self_occupied_address="")

    def clients(self, totals: list[int]) -> list[ClientW]:
        rng = self.rng
        out = []
        for i, total in enumerate(totals):
            name, prefix = CLIENTS[i % len(CLIENTS)]
            weights = [Decimal("0.2"), Decimal("0.3"), Decimal("0.25"), Decimal("0.25")]
            qs = [money(Decimal(total) * w) for w in weights]
            out.append(ClientW(name=name, tan=tan_for(rng, prefix), receipts_quarters=qs))
        return out

    def investments(self, elss: int = 0, ppf: int = 0, lic: int = 0, health: int = 0, nps_1b: int = 0) -> list[InvestmentW]:
        rng = self.rng
        out = []
        if elss:
            out.append(InvestmentW("ELSS", "Bluechip Tax Saver Fund", Decimal(elss), month_date(self.rules, rng.randint(0, 10), 10), "80C"))
        if ppf:
            out.append(InvestmentW("PPF", "Post Office PPF", Decimal(ppf), month_date(self.rules, 0, 5), "80C"))
        if lic:
            out.append(InvestmentW("LIFE_INSURANCE", "Jeevan Suraksha Plan", Decimal(lic), month_date(self.rules, 6, 15), "80C"))
        if health:
            out.append(InvestmentW("HEALTH_INSURANCE", "Care Shield Family Floater", Decimal(health), month_date(self.rules, 2, 20), "80D"))
        if nps_1b:
            out.append(InvestmentW("NPS", "NPS Tier I", Decimal(nps_1b), month_date(self.rules, 11, 5), "80CCD1B"))
        return out
