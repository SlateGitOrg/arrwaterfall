from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import date

from arrwaterfall.synth import FX_WINDOW_END, FX_WINDOW_START, MONTH_ENDS, generate


def read(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def test_generator_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate(a, seed=3)
    generate(b, seed=3)
    for name in ("customers.csv", "contract_lines.csv", "fx_rates.csv", "truth.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_a_different_seed_gives_a_different_book(tmp_path):
    generate(tmp_path / "a", seed=3)
    generate(tmp_path / "b", seed=4)
    assert (tmp_path / "a/contract_lines.csv").read_bytes() != (
        tmp_path / "b/contract_lines.csv"
    ).read_bytes()


def test_book_shape(data_dir):
    customers = read(data_dir / "customers.csv")
    lines = read(data_dir / "contract_lines.csv")
    assert len(customers) == 900
    assert 2800 <= len(lines) <= 3300
    assert Counter(c["currency"] for c in customers) == {"USD": 675, "EUR": 162, "GBP": 63}
    assert Counter(c["segment"] for c in customers) == {
        "SMB": 540,
        "Mid-market": 270,
        "Enterprise": 90,
    }
    assert len(MONTH_ENDS) == 48 and MONTH_ENDS[-1] == date(2026, 10, 31)


def test_fx_table_has_48_months_and_constant_plan_rates(data_dir):
    rows = read(data_dir / "fx_rates.csv")
    assert len(rows) == 48 * 3
    plan = {}
    for r in rows:
        plan.setdefault(r["currency"], set()).add(r["plan_usd"])
    assert plan == {"USD": {"1.0"}, "EUR": {"1.1"}, "GBP": {"1.27"}}


def test_eur_spot_jumps_8_percent_in_month_30(data_dir):
    eur = [r for r in read(data_dir / "fx_rates.csv") if r["currency"] == "EUR"]
    rates = [float(r["spot_usd"]) for r in eur]
    assert abs(rates[29] / rates[28] - 1.08) < 1e-5
    others = [abs(rates[i] / rates[i - 1] - 1) for i in range(1, 48) if i != 29]
    assert max(others) < 0.03


def test_truth_counts(truth):
    assert len(truth["late_renewals"]) == 40
    assert len(truth["true_churns"]) == 15
    assert sum(t["reactivates"] for t in truth["true_churns"]) == 6
    assert len(truth["reactivations"]) == 6
    assert len(truth["overlap_renewals"]) == 10
    gaps = [t["gap_days"] for t in truth["late_renewals"]]
    assert min(gaps) == 5 and max(gaps) == 85
    assert all(213 <= r["gap_days"] <= 304 for r in truth["reactivations"])


def test_each_late_renewal_gap_spans_a_month_end(truth):
    for ev in truth["late_renewals"]:
        lapse = date.fromisoformat(ev["lapse_after"])
        back = date.fromisoformat(ev["return_date"])
        assert (back - lapse).days - 1 == ev["gap_days"]
        assert any(lapse < d < back for d in MONTH_ENDS)


def test_injected_events_use_distinct_customers(truth):
    ids = (
        [e["customer_id"] for e in truth["late_renewals"]]
        + [e["customer_id"] for e in truth["true_churns"]]
        + [e["customer_id"] for e in truth["overlap_renewals"]]
    )
    assert len(ids) == len(set(ids)) == 65


def test_overlap_renewals_start_on_the_day_the_old_line_ends(data_dir, truth):
    lines = {r["line_id"]: r for r in read(data_dir / "contract_lines.csv")}
    for ev in truth["overlap_renewals"]:
        d = date.fromisoformat(ev["month_end"])
        assert d in MONTH_ENDS
        assert ev["old_line_ids"] and ev["new_line_ids"]
        assert all(lines[i]["end_date"] == ev["month_end"] for i in ev["old_line_ids"])
        assert all(lines[i]["start_date"] == ev["month_end"] for i in ev["new_line_ids"])
        assert all(lines[i]["replaces_line_id"] in ev["old_line_ids"] for i in ev["new_line_ids"])


def test_eur_customers_do_not_change_contract_in_the_fx_month(data_dir):
    eur = {c["customer_id"] for c in read(data_dir / "customers.csv") if c["currency"] == "EUR"}
    for r in read(data_dir / "contract_lines.csv"):
        if r["customer_id"] not in eur:
            continue
        start, end = date.fromisoformat(r["start_date"]), date.fromisoformat(r["end_date"])
        assert not FX_WINDOW_START <= start <= FX_WINDOW_END
        assert not date(2025, 3, 31) <= end <= date(2025, 4, 29)


def test_first_contract_date_is_the_earliest_line_start(data_dir):
    first: dict[str, str] = {}
    for r in read(data_dir / "contract_lines.csv"):
        first[r["customer_id"]] = min(first.get(r["customer_id"], "9999"), r["start_date"])
    for c in read(data_dir / "customers.csv"):
        assert c["first_contract_date"] == first[c["customer_id"]]


def test_truth_json_is_valid_json(data_dir):
    assert json.loads((data_dir / "truth.json").read_text())["seed"] == 11
