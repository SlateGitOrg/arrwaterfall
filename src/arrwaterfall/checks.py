"""Rigour checks that run after every build: the bridge cannot leak and closing ARR reconciles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .pipeline import Pipeline

TOLERANCE_USD = 0.01


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def independent_active_lc(data_dir: str | Path) -> pd.DataFrame:
    """Sum of annualised amounts of active, non-superseded lines per customer and month-end.

    Deliberately plain pandas, sharing no code with the SQL: a line counts at month-end d when
    start <= d <= end and no line that replaces it has started on or before d.
    """
    data_dir = Path(data_dir)
    lines = pd.read_csv(
        data_dir / "contract_lines.csv",
        parse_dates=["start_date", "end_date"],
        dtype={"replaces_line_id": str},
    )
    months = sorted(pd.to_datetime(pd.read_csv(data_dir / "fx_rates.csv")["month_end"].unique()))
    replaced_from = (
        lines.dropna(subset=["replaces_line_id"]).groupby("replaces_line_id")["start_date"].min()
    )
    lines["replaced_from"] = lines["line_id"].map(replaced_from)
    frames = []
    for d in months:
        live = lines[
            (lines["start_date"] <= d)
            & (lines["end_date"] >= d)
            & (lines["replaced_from"].isna() | (lines["replaced_from"] > d))
        ]
        total = live.groupby("customer_id")["annualised_amount"].sum().rename("independent_lc")
        frames.append(total.reset_index().assign(month_end=d))
    return pd.concat(frames, ignore_index=True)


def reconcile_closing(p: Pipeline) -> float:
    """Largest absolute difference between the SQL active ARR and the independent sum (LC)."""
    sql = p.query("SELECT customer_id, month_end, active_lc FROM customer_arr WHERE active_lc > 0")
    sql["month_end"] = pd.to_datetime(sql["month_end"])
    indep = independent_active_lc(p.data_dir)
    merged = sql.merge(indep, on=["customer_id", "month_end"], how="outer").fillna(0.0)
    return float((merged["active_lc"] - merged["independent_lc"]).abs().max())


def run_checks(p: Pipeline) -> list[CheckResult]:
    results: list[CheckResult] = []

    residual = p.query(
        "SELECT MAX(ABS(opening + new + expansion - contraction - churn + reactivation"
        " + fx - closing)) AS r FROM customer_bridge"
    )["r"][0]
    results.append(
        CheckResult(
            "bridge residual",
            residual <= TOLERANCE_USD,
            f"largest customer-month residual {residual:.6f} USD (limit {TOLERANCE_USD})",
        )
    )

    drift = reconcile_closing(p)
    results.append(
        CheckResult(
            "closing ARR vs independent sum of lines",
            drift <= TOLERANCE_USD,
            f"largest difference {drift:.6f} in contract currency",
        )
    )

    carried = p.query(
        "SELECT COALESCE(SUM(arr_lc - active_lc), 0) AS carried, "
        "COALESCE(SUM(arr_lc) FILTER (WHERE status = 'in_grace'), 0) AS in_grace FROM customer_arr"
    ).iloc[0]
    results.append(
        CheckResult(
            "closing ARR minus active lines equals in-grace ARR",
            abs(carried["carried"] - carried["in_grace"]) <= TOLERANCE_USD,
            f"in-grace ARR carried over all months {carried['in_grace']:,.0f}",
        )
    )

    bad_new = p.query(
        "SELECT COUNT(*) AS n FROM customer_movement m WHERE m.new_lc > 0 AND EXISTS ("
        "SELECT 1 FROM customer_arr a WHERE a.customer_id = m.customer_id "
        "AND a.month_idx < m.month_idx AND a.arr_lc > 0)"
    )["n"][0]
    dup_new = p.query(
        "SELECT COUNT(*) AS n FROM (SELECT customer_id FROM customer_movement WHERE new_lc > 0 "
        "GROUP BY customer_id HAVING COUNT(*) > 1)"
    )["n"][0]
    results.append(
        CheckResult(
            "no returning customer is booked as new",
            bad_new == 0 and dup_new == 0,
            f"{bad_new} new bookings after earlier ARR, {dup_new} customers with two",
        )
    )

    bad_rate = p.query(
        "SELECT COUNT(*) AS n FROM monthly_metrics "
        "WHERE grr_ttm > 1 + 1e-9 OR grr_ttm > nrr_ttm + 1e-9"
    )["n"][0]
    results.append(
        CheckResult(
            "GRR <= 1 and GRR <= NRR in every month", bad_rate == 0, f"{bad_rate} violating months"
        )
    )
    return results
