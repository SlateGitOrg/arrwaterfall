"""Seeded synthetic book of B2B SaaS contracts with deliberately injected ARR events.

Properties of the generated book (all documented, all recovered by the tests):

* 900 customers (60% SMB, 30% mid-market, 10% enterprise; 75% USD, 18% EUR, 7% GBP).
* 48 month-ends ending 2026-10-31.  About 30% of customers signed before the window.
* Annual, 2-year and 3-year terms; every renewal is on time and contiguous unless it is one
  of the injected events below, so the injected events are the only lapses in the book.
* Seat upgrades replace the running line from a mid-term date, co-termed add-ons start mid-term,
  and renewals can drop an add-on or downgrade the platform line.
* Injected: 40 late renewals (gap 5-85 days, each spanning a month-end), 15 true churns of which
  6 come back 7-10 months later, 10 renewals booked with a one-day overlap on a month-end, and
  a +8% EUR spot move in month 30 with no EUR contract change in that month.
* `truth.json` lists every injected event so the tests can check recovery.
"""

from __future__ import annotations

import bisect
import calendar
import copy
import csv
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

ONE = timedelta(days=1)
HORIZON = date(2026, 10, 31)
N_MONTHS = 48
FX_EVENT_MONTH = 30  # 1-based month index of the EUR spot move
FX_EVENT_PCT = 0.08
PLAN_RATES = {"USD": 1.0, "EUR": 1.10, "GBP": 1.27}
START_SPOT = {"USD": 1.0, "EUR": 1.045, "GBP": 1.19}
SPOT_VOL = {"USD": 0.0, "EUR": 0.007, "GBP": 0.008}

N_CUSTOMERS = 900
SEGMENT_SHARE = (("SMB", 0.60), ("Mid-market", 0.30), ("Enterprise", 0.10))
CURRENCY_SHARE = (("USD", 0.75), ("EUR", 0.18), ("GBP", 0.07))
ADDON_PRODUCTS = ("Analytics", "API Access", "Support Plus", "Sandbox")

N_LATE = 40
N_TRUE_CHURN = 15
N_REACTIVATE = 6
N_OVERLAP = 10


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def month_ends(n: int = N_MONTHS, last: date = HORIZON) -> list[date]:
    out = []
    y, m = last.year, last.month
    for _ in range(n):
        out.append(month_end(y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


MONTH_ENDS = month_ends()
FX_WINDOW_START = date(MONTH_ENDS[FX_EVENT_MONTH - 1].year, MONTH_ENDS[FX_EVENT_MONTH - 1].month, 1)
FX_WINDOW_END = MONTH_ENDS[FX_EVENT_MONTH - 1]


def add_years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:  # 29 February
        return d.replace(year=d.year + n, day=28)


def _round100(x: float) -> int:
    return max(100, int(round(x / 100.0)) * 100)


@dataclass
class Line:
    product: str
    start: date
    end: date
    amount: int
    term: int
    replaces: Line | None = None
    line_id: str = ""


@dataclass
class Term:
    index: int
    start: date
    end: date
    lines: list[Line] = field(default_factory=list)
    dropped: bool = False  # an add-on was not renewed at the start of this term


@dataclass
class Customer:
    index: int
    segment: str
    currency: str
    terms: list[Term]
    customer_id: str = ""

    @property
    def first_start(self) -> date:
        return self.terms[0].start

    def lines(self) -> list[Line]:
        return [ln for t in self.terms for ln in t.lines]


def _draw_amount(rng: random.Random, segment: str) -> int:
    if segment == "SMB":
        return _round100(min(30000, max(2000, rng.lognormvariate(9.1, 0.45))))
    if segment == "Mid-market":
        return _round100(min(150000, max(15000, rng.lognormvariate(10.7, 0.40))))
    return _round100(min(900000, max(90000, rng.lognormvariate(12.3, 0.45))))


def _build_terms(
    rng: random.Random, start: date, platform: int, first_index: int = 0, renewal: bool = False
) -> list[Term]:
    """Contiguous terms from `start` until the horizon, with mid-term events and renewal changes."""
    terms: list[Term] = []
    last: dict[str, Line] = {}
    addons: dict[str, int] = {}
    t, idx = start, first_index
    while t <= HORIZON:
        length = rng.choices((1, 2, 3), weights=(55, 25, 20))[0]
        end = add_years(t, length) - ONE
        dropped = False
        if renewal or idx > first_index:
            r = rng.random()
            if r < 0.16:
                platform = _round100(platform * rng.uniform(0.55, 0.90))
            elif r < 0.55:
                platform = _round100(platform * rng.uniform(1.00, 1.06))
            for product in list(addons):
                if rng.random() < 0.40:
                    del addons[product]
                    last.pop(product, None)
                    dropped = True
                else:
                    addons[product] = _round100(addons[product] * rng.uniform(1.0, 1.04))
        term = Term(idx, t, end, dropped=dropped)

        def make(product: str, s: date, amount: int, term: Term = term, end: date = end) -> None:
            line = Line(product, s, end, amount, term.index, replaces=last.get(product))
            last[product] = line
            term.lines.append(line)

        make("Platform", t, platform)
        for product, amount in addons.items():
            make(product, t, amount)

        event_days: list[int] = []
        for year in range(length):
            if rng.random() < 0.25:
                offset = year * 365 + rng.randint(60, 305)
                day = t + timedelta(days=offset)
                if day <= end - timedelta(days=45) and all(
                    abs(offset - o) >= 30 for o in event_days
                ):
                    event_days.append(offset)
        for offset in sorted(event_days):
            day = t + timedelta(days=offset)
            free = [p for p in ADDON_PRODUCTS if p not in addons]
            if rng.random() < 0.60 or not free:
                platform = _round100(platform * rng.uniform(1.08, 1.35))
                make("Platform", day, platform)
            else:
                product = rng.choice(free)
                addons[product] = _round100(platform * rng.uniform(0.08, 0.25))
                make(product, day, addons[product])
        terms.append(term)
        t, idx = end + ONE, idx + 1
    return terms


def _violates_fx_window(terms: list[Term], currency: str) -> bool:
    """EUR customers may not change contract in the month of the EUR spot move."""
    if currency != "EUR":
        return False
    for term in terms:
        for ln in term.lines:
            if FX_WINDOW_START <= ln.start <= FX_WINDOW_END:
                return True
            if FX_WINDOW_START - ONE <= ln.end <= FX_WINDOW_END - ONE:
                return True
    return False


def _make_customer(seed: int, index: int, segment: str, currency: str, start: date) -> Customer:
    if currency == "EUR" and FX_WINDOW_START <= start <= FX_WINDOW_END:
        start = FX_WINDOW_END + ONE
    for attempt in range(500):
        rng = random.Random(f"{seed}:{index}:{attempt}")
        terms = _build_terms(rng, start, _draw_amount(rng, segment))
        if not _violates_fx_window(terms, currency):
            return Customer(index, segment, currency, terms)
    raise RuntimeError("could not build a customer clear of the FX window")


def _draw_start(rng: random.Random, pre_window: bool) -> date:
    if pre_window:
        day = date(2021, 1, 1) + timedelta(
            days=rng.randint(0, (date(2022, 11, 15) - date(2021, 1, 1)).days)
        )
    else:
        lo, hi = date(2022, 12, 1), date(2026, 9, 15)
        span = (hi - lo).days
        day = lo + timedelta(days=int(span * rng.random() ** 0.8))
    if rng.random() < 0.45:
        day = day.replace(day=1)
    return day


def _shift(terms: list[Term], from_term: int, days: int) -> None:
    delta = timedelta(days=days)
    for term in terms[from_term:]:
        term.start += delta
        term.end += delta
        for ln in term.lines:
            ln.start += delta
            ln.end += delta


def _has_month_end_between(lo: date, hi: date) -> bool:
    """True if a month-end d satisfies lo < d < hi."""
    i = bisect.bisect_right(MONTH_ENDS, lo)
    return i < len(MONTH_ENDS) and MONTH_ENDS[i] < hi


def _fx_rates(seed: int) -> list[dict[str, object]]:
    rng = random.Random(f"{seed}:fx")
    rows: list[dict[str, object]] = []
    spot = dict(START_SPOT)
    for idx, me in enumerate(MONTH_ENDS, start=1):
        for ccy in ("USD", "EUR", "GBP"):
            if idx == FX_EVENT_MONTH and ccy == "EUR":
                spot[ccy] *= 1.0 + FX_EVENT_PCT  # the move itself, with no random drift on top
            elif idx > 1 and SPOT_VOL[ccy]:
                spot[ccy] *= 1.0 + rng.gauss(0.0, SPOT_VOL[ccy])
            spot[ccy] = round(spot[ccy], 6)
            rows.append(
                {
                    "month_end": me.isoformat(),
                    "currency": ccy,
                    "spot_usd": spot[ccy],
                    "plan_usd": PLAN_RATES[ccy],
                }
            )
    return rows


def _allocate(rng: random.Random, n: int, shares: tuple[tuple[str, float], ...]) -> list[str]:
    labels: list[str] = []
    for name, share in shares:
        labels += [name] * int(round(share * n))
    while len(labels) < n:
        labels.append(shares[0][0])
    labels = labels[:n]
    rng.shuffle(labels)
    return labels


class _Injector:
    """Applies the injected events to customers, keeping every customer in at most one event."""

    def __init__(self, customers: list[Customer], rng: random.Random):
        self.customers = customers
        self.rng = rng
        self.used: set[int] = set()
        pool = [
            (c.index, k, c.terms[k].start - ONE) for c in customers for k in range(1, len(c.terms))
        ]
        rng.shuffle(pool)
        self.pool = pool

    def _candidates(self, lo: date, hi: date):
        for idx, k, e in self.pool:
            if idx not in self.used and lo <= e <= hi:
                yield self.customers[idx], k, e

    def _commit(self, cust: Customer, terms: list[Term]) -> None:
        cust.terms = terms
        self.used.add(cust.index)

    def churn(self, lo: date, hi: date, reactivate_gap: int | None = None):
        for cust, k, e in self._candidates(lo, hi):
            terms = copy.deepcopy(cust.terms[:k])
            if reactivate_gap is not None:
                platform = [ln for ln in terms[-1].lines if ln.product == "Platform"][-1].amount
                back = e + timedelta(days=reactivate_gap + 1)
                rng = random.Random(f"{cust.index}:{e.isoformat()}:back")
                terms += _build_terms(
                    rng,
                    back,
                    _round100(platform * rng.uniform(0.6, 1.1)),
                    first_index=k,
                    renewal=False,
                )
            if _violates_fx_window(terms, cust.currency):
                continue
            self._commit(cust, terms)
            return cust, e
        raise RuntimeError("no churn candidate")

    def late(self, gap: int):
        for cust, k, e in self._candidates(date(2023, 1, 1), date(2026, 6, 30)):
            if not _has_month_end_between(e, e + timedelta(days=gap + 1)):
                continue
            terms = copy.deepcopy(cust.terms)
            _shift(terms, k, gap)
            if _violates_fx_window(terms, cust.currency):
                continue
            self._commit(cust, terms)
            return cust, e
        raise RuntimeError("no late-renewal candidate")

    def overlap(self):
        for cust, k, e in self._candidates(date(2023, 1, 31), date(2026, 9, 30)):
            if e != month_end(e.year, e.month) or cust.terms[k].dropped:
                continue
            terms = copy.deepcopy(cust.terms)
            _shift(terms, k, -1)
            if _violates_fx_window(terms, cust.currency):
                continue
            self._commit(cust, terms)
            return cust, e
        raise RuntimeError("no overlap candidate")


def _late_gaps(rng: random.Random) -> list[int]:
    gaps = [5, 30, 31, 60, 61, 85]
    gaps += [rng.randint(6, 29) for _ in range(9)]
    gaps += [rng.randint(32, 59) for _ in range(9)]
    gaps += [rng.randint(62, 84) for _ in range(16)]
    assert len(gaps) == N_LATE
    rng.shuffle(gaps)
    return gaps


def generate(out_dir: str | Path, seed: int = 11, n_customers: int = N_CUSTOMERS) -> dict:
    """Write customers.csv, contract_lines.csv, fx_rates.csv and truth.json into `out_dir`."""
    if n_customers < 400:
        raise ValueError("at least 400 customers are needed to place the injected events")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    segments = _allocate(rng, n_customers, SEGMENT_SHARE)
    currencies = _allocate(rng, n_customers, CURRENCY_SHARE)
    customers = [
        _make_customer(seed, i, segments[i], currencies[i], _draw_start(rng, rng.random() < 0.28))
        for i in range(n_customers)
    ]

    inj = _Injector(customers, random.Random(f"{seed}:inject"))
    churn_log, react_log, late_log, overlap_log = [], [], [], []
    for _ in range(N_REACTIVATE):
        gap = inj.rng.randint(213, 304)  # about 7 to 10 months
        cust, e = inj.churn(date(2023, 3, 1), date(2026, 1, 31), reactivate_gap=gap)
        churn_log.append((cust, e, True))
        react_log.append((cust, e, gap))
    for _ in range(N_TRUE_CHURN - N_REACTIVATE):
        cust, e = inj.churn(date(2023, 3, 1), date(2026, 7, 31))
        churn_log.append((cust, e, False))
    for gap in _late_gaps(inj.rng):
        cust, e = inj.late(gap)
        late_log.append((cust, e, gap))
    for _ in range(N_OVERLAP):
        cust, e = inj.overlap()
        overlap_log.append((cust, e))

    # Stable ids: customers by first contract date, lines by customer then start then product.
    ordered = sorted(customers, key=lambda c: (c.first_start, c.index))
    for n, cust in enumerate(ordered, start=1):
        cust.customer_id = f"C{n:04d}"
    all_lines = sorted(
        ((c, ln) for c in ordered for ln in c.lines()),
        key=lambda cl: (cl[0].customer_id, cl[1].start, cl[1].product, cl[1].end),
    )
    for n, (_, ln) in enumerate(all_lines, start=1):
        ln.line_id = f"L{n:05d}"

    with open(out / "customers.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["customer_id", "segment", "currency", "first_contract_date"])
        for c in ordered:
            w.writerow([c.customer_id, c.segment, c.currency, c.first_start.isoformat()])
    with open(out / "contract_lines.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "line_id",
                "customer_id",
                "product",
                "start_date",
                "end_date",
                "annualised_amount",
                "replaces_line_id",
            ]
        )
        for c, ln in all_lines:
            w.writerow(
                [
                    ln.line_id,
                    c.customer_id,
                    ln.product,
                    ln.start.isoformat(),
                    ln.end.isoformat(),
                    ln.amount,
                    ln.replaces.line_id if ln.replaces else "",
                ]
            )
    with open(out / "fx_rates.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["month_end", "currency", "spot_usd", "plan_usd"])
        w.writeheader()
        w.writerows(_fx_rates(seed))

    def new_lines_at(c: Customer, d: date) -> list[Line]:
        return [ln for ln in c.lines() if ln.start == d]

    truth = {
        "seed": seed,
        "horizon": HORIZON.isoformat(),
        "n_customers": n_customers,
        "n_lines": len(all_lines),
        "gap_days_definition": (
            "uncovered days between the last day of the old contract and the first day of the next"
        ),
        "late_renewals": [
            {
                "customer_id": c.customer_id,
                "lapse_after": e.isoformat(),
                "return_date": (e + timedelta(days=g + 1)).isoformat(),
                "gap_days": g,
            }
            for c, e, g in sorted(late_log, key=lambda x: x[0].customer_id)
        ],
        "true_churns": [
            {"customer_id": c.customer_id, "lapse_after": e.isoformat(), "reactivates": r}
            for c, e, r in sorted(churn_log, key=lambda x: x[0].customer_id)
        ],
        "reactivations": [
            {
                "customer_id": c.customer_id,
                "lapse_after": e.isoformat(),
                "return_date": (e + timedelta(days=g + 1)).isoformat(),
                "gap_days": g,
            }
            for c, e, g in sorted(react_log, key=lambda x: x[0].customer_id)
        ],
        "overlap_renewals": [
            {
                "customer_id": c.customer_id,
                "month_end": e.isoformat(),
                "old_line_ids": sorted(ln.line_id for ln in c.lines() if ln.end == e),
                "new_line_ids": sorted(ln.line_id for ln in new_lines_at(c, e)),
            }
            for c, e in sorted(overlap_log, key=lambda x: x[0].customer_id)
        ],
        "fx_event": {
            "currency": "EUR",
            "month_idx": FX_EVENT_MONTH,
            "month_end": FX_WINDOW_END.isoformat(),
            "spot_change": FX_EVENT_PCT,
        },
    }
    (out / "truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    return truth
