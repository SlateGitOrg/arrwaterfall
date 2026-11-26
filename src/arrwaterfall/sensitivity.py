"""Re-runs the pipeline under several grace windows and reports how the definitions move."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .metrics import SENSITIVITY_GRACE_DAYS
from .pipeline import run_pipeline


def sensitivity(
    data_dir: str | Path, grace_days: tuple[int, ...] = SENSITIVITY_GRACE_DAYS
) -> pd.DataFrame:
    """One row per grace window.  ARR is constant currency, totals cover the whole bridge."""
    rows = []
    for g in grace_days:
        with run_pipeline(data_dir, grace_days=g) as p:
            tot = p.query(
                "SELECT SUM(churn) AS churn_arr, SUM(reactivation) AS reactivation_arr, "
                "SUM(churned_logos) AS churned_logos, SUM(reactivated_logos) AS reactivated_logos "
                "FROM bridge WHERE view = 'constant'"
            ).iloc[0]
            met = p.query(
                "SELECT nrr_ttm, grr_ttm FROM monthly_metrics WHERE nrr_ttm IS NOT NULL "
                "ORDER BY month_idx"
            )
        rows.append(
            {
                "grace_days": g,
                "churned_logos": int(tot["churned_logos"]),
                "churn_arr": float(tot["churn_arr"]),
                "reactivated_logos": int(tot["reactivated_logos"]),
                "reactivation_arr": float(tot["reactivation_arr"]),
                "grr_ttm_last": float(met["grr_ttm"].iloc[-1]),
                "grr_ttm_mean": float(met["grr_ttm"].mean()),
                "nrr_ttm_last": float(met["nrr_ttm"].iloc[-1]),
            }
        )
    return pd.DataFrame(rows)
