"""A five-customer, 14-month book whose answers are worked out by hand.

Month index: 1 = 2025-01-31 ... 12 = 2025-12-31, 13 = 2026-01-31, 14 = 2026-02-28.
EUR plan rate 1.10 throughout; EUR spot is 1.10 until month 5 and 1.21 from month 6.

F1 USD  12,000 to 2025-03-31, renewed 2025-05-10 at 14,400 (39 uncovered days: Apr 1 - May 9)
F2 USD  20,000 to 2025-08-31, co-termed upgrade to 30,000 from 2025-04-15, renewed at 30,000
F3 EUR  10,000 to 2025-05-31, renewed 2025-06-01 at EUR 8,000
F4 USD   5,000 to 2025-12-31, never renewed
F5 USD   8,000 from 2025-07-01 (new)
"""

from __future__ import annotations

import pytest


def bridge_row(p, view: str, month_idx: int):
    return p.query(f"SELECT * FROM bridge WHERE view = '{view}' AND month_idx = {month_idx}").iloc[
        0
    ]


def test_upgrade_replaces_the_line_it_supersedes(fixture_pipe_factory):
    p = fixture_pipe_factory(60)
    # Month 4 (2025-04-30): only the 30,000 upgrade counts.  Without the supersede rule the
    # 20,000 line (still running to August) would make F2 50,000.
    f2 = p.query("SELECT arr_lc FROM customer_arr WHERE customer_id = 'F2' AND month_idx = 4")
    assert f2["arr_lc"][0] == 30_000
    assert bridge_row(p, "constant", 4)["expansion"] == 10_000


def test_late_renewal_inside_grace_is_expansion_not_churn(fixture_pipe_factory):
    p = fixture_pipe_factory(60)
    status = p.query(
        "SELECT month_idx, status, arr_lc FROM customer_arr WHERE customer_id = 'F1' "
        "AND month_idx BETWEEN 3 AND 5 ORDER BY month_idx"
    )
    assert list(status["status"]) == ["active", "in_grace", "active"]
    assert list(status["arr_lc"]) == [12_000, 12_000, 14_400]
    may = bridge_row(p, "constant", 5)
    assert may["expansion"] == 2_400
    assert may["churn"] == 0 and may["reactivation"] == 0 and may["new"] == 0


def test_same_late_renewal_is_churn_plus_reactivation_in_one_month_at_30_days(
    fixture_pipe_factory,
):
    # gap is 39 days > 30: churn is booked in the month holding Mar 31 + 31 days = May 1,
    # and the return on May 10 is a reactivation in the same month.
    p = fixture_pipe_factory(30)
    may = bridge_row(p, "constant", 5)
    assert may["churn"] == 12_000
    assert may["reactivation"] == 14_400
    assert may["expansion"] == 0
    assert bridge_row(p, "constant", 4)["churn"] == 0  # Apr 30 is 30 days out: still in grace


def test_zero_grace_books_churn_in_the_first_month_of_the_gap(fixture_pipe_factory):
    p = fixture_pipe_factory(0)
    assert bridge_row(p, "constant", 4)["churn"] == 12_000
    assert bridge_row(p, "constant", 5)["reactivation"] == 14_400


def test_spot_view_splits_the_eur_downgrade_and_the_rate_move(fixture_pipe_factory):
    p = fixture_pipe_factory(60)
    # Month 6, F3: opening 10,000 EUR at 1.10 = 11,000; downgrade of 2,000 EUR priced at the
    # new rate 1.21 = 2,420; FX = 10,000 x (1.21 - 1.10) = 1,100; closing 8,000 x 1.21 = 9,680.
    row = p.query(
        "SELECT * FROM customer_bridge WHERE customer_id = 'F3' AND month_idx = 6 AND view = 'spot'"
    ).iloc[0]
    assert row["opening"] == pytest.approx(11_000)
    assert row["contraction"] == pytest.approx(2_420)
    assert row["fx"] == pytest.approx(1_100)
    assert row["closing"] == pytest.approx(9_680)
    const = p.query(
        "SELECT * FROM customer_bridge WHERE customer_id = 'F3' AND month_idx = 6 "
        "AND view = 'constant'"
    ).iloc[0]
    assert const["contraction"] == pytest.approx(2_200)
    assert const["fx"] == 0


def test_new_customer_is_new_exactly_once(fixture_pipe_factory):
    p = fixture_pipe_factory(60)
    new = p.query(
        "SELECT month_idx, new FROM customer_bridge WHERE customer_id = 'F5' "
        "AND view = 'constant' AND new > 0"
    )
    assert list(new["month_idx"]) == [7]
    assert new["new"][0] == 8_000


def test_ttm_nrr_and_grr_with_60_day_grace(fixture_pipe_factory):
    p = fixture_pipe_factory(60)
    # Month 14 looks back to month 2 (2025-02-28).  Base customers and their constant-currency
    # ARR then: F1 12,000; F2 20,000; F3 10,000 EUR x 1.10 = 11,000; F4 5,000 = 48,000.
    # Now: F1 14,400; F2 30,000; F3 8,000 EUR x 1.10 = 8,800; F4 lapsed 59 days ago, still in
    # grace, carried at 5,000 (F5 was not a customer in month 2, so it is outside the base).
    # NRR = (14,400 + 30,000 + 8,800 + 5,000) / 48,000 = 58,200 / 48,000 = 1.2125
    # GRR = (12,000 + 20,000 + 8,800 + 5,000) / 48,000 = 45,800 / 48,000 = 0.954166...
    m = p.query("SELECT * FROM monthly_metrics WHERE month_idx = 14").iloc[0]
    assert m["nrr_ttm"] == pytest.approx(58_200 / 48_000)
    assert m["grr_ttm"] == pytest.approx(45_800 / 48_000)
    assert m["closing_arr"] == pytest.approx(14_400 + 30_000 + 8_800 + 5_000 + 8_000)


def test_ttm_nrr_and_grr_with_30_day_grace(fixture_pipe_factory):
    p = fixture_pipe_factory(30)
    # F4 is now churned (churn booked in month 13), so it contributes 0.
    # NRR = (14,400 + 30,000 + 8,800) / 48,000 = 1.108333...
    # GRR = (12,000 + 20,000 + 8,800) / 48,000 = 0.85
    m = p.query("SELECT * FROM monthly_metrics WHERE month_idx = 14").iloc[0]
    assert m["nrr_ttm"] == pytest.approx(53_200 / 48_000)
    assert m["grr_ttm"] == pytest.approx(0.85)
    assert bridge_row(p, "constant", 13)["churn"] == 5_000


def test_no_ttm_before_twelve_months_of_history(fixture_pipe_factory):
    p = fixture_pipe_factory(60)
    m = p.query(
        "SELECT month_idx FROM monthly_metrics WHERE nrr_ttm IS NOT NULL ORDER BY month_idx"
    )
    assert list(m["month_idx"]) == [13, 14]


def test_fixture_bridge_closes_for_every_customer_month(fixture_pipe_factory):
    for grace in (0, 30, 60, 90):
        p = fixture_pipe_factory(grace)
        worst = p.query(
            "SELECT MAX(ABS(opening + new + expansion - contraction - churn + reactivation "
            "+ fx - closing)) AS r FROM customer_bridge"
        )["r"][0]
        assert worst < 1e-6
