"""Acceptance properties of the bridge on the full synthetic book."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from arrwaterfall.checks import independent_active_lc, reconcile_closing, run_checks

GRACES = (0, 30, 60, 90)


@pytest.mark.parametrize("grace", GRACES)
def test_bridge_residual_is_zero_in_both_views(pipe_factory, grace):
    p = pipe_factory(grace)
    residual = p.query(
        "SELECT view, MAX(ABS(opening + new + expansion - contraction - churn + reactivation + fx"
        " - closing)) AS r FROM customer_bridge GROUP BY view"
    )
    assert set(residual["view"]) == {"constant", "spot"}
    assert (residual["r"] < 0.01).all()


def test_constant_view_never_has_fx(pipe):
    assert (
        pipe.query("SELECT MAX(ABS(fx)) AS m FROM customer_bridge WHERE view = 'constant'")["m"][0]
        == 0
    )


def test_closing_matches_independent_sum_of_active_lines(pipe_factory):
    # At 0 grace nothing is carried, so the reported closing ARR is exactly the sum of lines.
    p = pipe_factory(0)
    indep = independent_active_lc(p.data_dir)
    sql = p.query("SELECT customer_id, month_end, arr_lc FROM customer_arr WHERE arr_lc > 0")
    sql["month_end"] = pd.to_datetime(sql["month_end"])
    merged = sql.merge(indep, on=["customer_id", "month_end"], how="outer").fillna(0.0)
    assert len(merged) > 20_000
    assert (merged["arr_lc"] - merged["independent_lc"]).abs().max() == 0


def test_active_arr_matches_independent_sum_for_any_grace(pipe):
    assert reconcile_closing(pipe) == 0
    carried = pipe.query("SELECT SUM(arr_lc - active_lc) AS c FROM customer_arr")["c"][0]
    in_grace = pipe.query("SELECT SUM(arr_lc) AS c FROM customer_arr WHERE status = 'in_grace'")[
        "c"
    ][0]
    assert carried == in_grace > 0


def test_overlap_renewals_count_the_renewal_once(pipe, truth, data_dir):
    lines = pd.read_csv(data_dir / "contract_lines.csv").set_index("line_id")
    for ev in truth["overlap_renewals"]:
        d = date.fromisoformat(ev["month_end"])
        renewal = lines.loc[ev["new_line_ids"], "annualised_amount"].sum()
        old = lines.loc[ev["old_line_ids"], "annualised_amount"].sum()
        row = pipe.query(
            f"SELECT active_lc FROM customer_arr WHERE customer_id = '{ev['customer_id']}' "
            f"AND month_end = DATE '{d}'"
        )
        assert row["active_lc"][0] == renewal
        # the naive sum of raw dates would also include the old lines on their last day
        raw = pipe.query(
            f"SELECT SUM(annualised_amount) AS s FROM contract_lines WHERE customer_id = "
            f"'{ev['customer_id']}' AND start_date <= DATE '{d}' AND end_date >= DATE '{d}'"
        )["s"][0]
        assert raw == renewal + old


@pytest.mark.parametrize("grace", GRACES)
def test_churn_and_reactivation_counts_follow_truth(pipe_factory, truth, grace):
    p = pipe_factory(grace)
    late_over = sum(1 for e in truth["late_renewals"] if e["gap_days"] > grace)
    totals = p.query(
        "SELECT SUM(churned_logos) AS c, SUM(reactivated_logos) AS r "
        "FROM bridge WHERE view = 'constant'"
    ).iloc[0]
    assert totals["c"] == 15 + late_over
    assert totals["r"] == 6 + late_over


def test_at_90_days_every_late_renewal_is_a_renewal(pipe_factory, truth):
    p = pipe_factory(90)
    churned = set(
        p.query("SELECT DISTINCT customer_id FROM customer_movement WHERE churn_lc > 0")[
            "customer_id"
        ]
    )
    late = {e["customer_id"] for e in truth["late_renewals"]}
    assert not churned & late
    assert churned == {e["customer_id"] for e in truth["true_churns"]}


def test_zero_grace_turns_every_late_renewal_into_churn_and_reactivation(pipe_factory, truth):
    p = pipe_factory(0)
    churned = set(
        p.query("SELECT DISTINCT customer_id FROM customer_movement WHERE churn_lc > 0")[
            "customer_id"
        ]
    )
    reactivated = set(
        p.query("SELECT DISTINCT customer_id FROM customer_movement WHERE reactivation_lc > 0")[
            "customer_id"
        ]
    )
    late = {e["customer_id"] for e in truth["late_renewals"]}
    assert late <= churned and late <= reactivated


@pytest.mark.parametrize("grace", GRACES)
def test_no_returning_customer_is_new(pipe_factory, grace):
    p = pipe_factory(grace)
    bad = p.query(
        "SELECT COUNT(*) AS n FROM customer_movement m WHERE m.new_lc > 0 "
        "AND EXISTS (SELECT 1 FROM customer_arr a WHERE a.customer_id = m.customer_id "
        "AND a.month_idx < m.month_idx AND a.arr_lc > 0)"
    )["n"][0]
    assert bad == 0
    per_customer = p.query(
        "SELECT MAX(n) AS m FROM (SELECT COUNT(*) AS n FROM customer_movement "
        "WHERE new_lc > 0 GROUP BY customer_id)"
    )["m"][0]
    assert per_customer == 1


def test_reactivations_come_7_to_10_months_after_the_lapse(pipe_factory, truth):
    p = pipe_factory(60)
    for ev in truth["reactivations"]:
        row = p.query(
            f"SELECT month_idx FROM customer_movement WHERE customer_id = "
            f"'{ev['customer_id']}' AND reactivation_lc > 0"
        )
        lapse = pd.Timestamp(ev["lapse_after"])
        back = pd.Timestamp(ev["return_date"])
        assert len(row) == 1
        months = (back.year - lapse.year) * 12 + back.month - lapse.month
        assert 7 <= months <= 10


def test_a_gap_just_over_the_window_is_churn_and_at_the_window_is_not(pipe_factory, truth):
    # gap_days of exactly 30 and 60 exist in the book: they are renewals at that window.
    for grace in (30, 60):
        p = pipe_factory(grace)
        exact = [e for e in truth["late_renewals"] if e["gap_days"] == grace][0]["customer_id"]
        over = [e for e in truth["late_renewals"] if e["gap_days"] == grace + 1][0]["customer_id"]
        churned = set(
            p.query("SELECT DISTINCT customer_id FROM customer_movement WHERE churn_lc > 0")[
                "customer_id"
            ]
        )
        assert exact not in churned
        assert over in churned


def test_spot_view_isolates_the_eur_move_in_month_30(pipe, data_dir):
    rates = pd.read_csv(data_dir / "fx_rates.csv")
    eur = rates[rates["currency"] == "EUR"].reset_index(drop=True)
    r29, r30 = eur.loc[28, "spot_usd"], eur.loc[29, "spot_usd"]
    prior = pipe.query(
        "SELECT SUM(arr_lc) AS s FROM customer_arr WHERE currency = 'EUR' AND month_idx = 29"
    )["s"][0]
    spot = pipe.query(
        "SELECT SUM(fx) AS fx, SUM(expansion) AS e, SUM(contraction) AS c FROM customer_bridge "
        "WHERE currency = 'EUR' AND month_idx = 30 AND view = 'spot'"
    ).iloc[0]
    assert spot["fx"] == pytest.approx(prior * (r30 - r29), abs=1.0)
    assert spot["fx"] == pytest.approx(0.08 * prior * r29, rel=1e-3)
    const = pipe.query(
        "SELECT SUM(expansion) AS e, SUM(contraction) AS c, SUM(fx) AS fx FROM customer_bridge "
        "WHERE currency = 'EUR' AND month_idx = 30 AND view = 'constant'"
    ).iloc[0]
    assert const["e"] == 0 and const["c"] == 0 and const["fx"] == 0
    # no EUR customer's ARR changed that month
    changed = pipe.query(
        "SELECT COUNT(*) AS n FROM customer_movement WHERE currency = 'EUR' AND month_idx = 30 "
        "AND open_lc <> close_lc"
    )["n"][0]
    assert changed == 0


def test_other_months_have_small_fx_in_the_spot_view(pipe):
    fx = pipe.query(
        "SELECT month_idx, fx / opening AS ratio FROM bridge WHERE view = 'spot' ORDER BY month_idx"
    )
    jump = fx[fx["month_idx"] == 30]["ratio"].iloc[0]
    rest = fx[fx["month_idx"] != 30]["ratio"].abs().max()
    assert jump > 2 * rest


@pytest.mark.parametrize("grace", GRACES)
def test_grr_is_at_most_one_and_at_most_nrr(pipe_factory, grace):
    m = pipe_factory(grace).query("SELECT * FROM monthly_metrics WHERE nrr_ttm IS NOT NULL")
    assert len(m) == 36
    assert (m["grr_ttm"] <= 1 + 1e-12).all()
    assert (m["grr_ttm"] <= m["nrr_ttm"] + 1e-12).all()


def test_longer_grace_never_lowers_mean_grr(pipe_factory):
    means = [
        pipe_factory(g).query("SELECT AVG(grr_ttm) AS m FROM monthly_metrics")["m"][0]
        for g in GRACES
    ]
    assert means == sorted(means)
    assert means[-1] - means[0] > 0.001


def test_cohort_nrr_starts_at_one_and_covers_the_last_cohorts(pipe):
    c = pipe.table("cohort_nrr")
    assert (c[c["age_months"] == 0]["nrr"].round(12) == 1.0).all()
    assert "2025-Q3" in set(c["cohort"])


def test_run_checks_all_pass(pipe):
    results = run_checks(pipe)
    assert len(results) == 5
    assert all(r.passed for r in results), [r for r in results if not r.passed]
