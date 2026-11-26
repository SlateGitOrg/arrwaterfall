"""Plain markdown tables for the CLI and the README."""

from __future__ import annotations

import pandas as pd

from .metrics import BRIDGE_COLUMNS, MOVEMENT_SIGN
from .pipeline import Pipeline

COHORT_AGES = (3, 6, 9, 12)


def markdown_table(header: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(header[i]), *(len(r[i]) for r in rows)) for i in range(len(header))]

    def fmt(cells: list[str]) -> str:
        padded = [c.rjust(widths[i]) if i else c.ljust(widths[i]) for i, c in enumerate(cells)]
        return "| " + " | ".join(padded) + " |"

    sep = (
        "|"
        + "|".join(
            ("-" * (w + 1)) + ":" if i else (":" + "-" * (w + 1)) for i, w in enumerate(widths)
        )
        + "|"
    )
    return "\n".join([fmt(header), sep, *(fmt(r) for r in rows)])


def bridge_table(p: Pipeline, view: str = "spot", months: int = 12) -> str:
    """Last `months` months of the bridge, USD.  Contraction and churn are shown negative."""
    df = p.query(f"SELECT * FROM bridge WHERE view = '{view}' ORDER BY month_idx").tail(months)
    header = ["Month", *(c.upper() if c == "fx" else c.capitalize() for c in BRIDGE_COLUMNS)]
    rows = []
    for rec in df.to_dict("records"):
        cells = [rec["month_end"].strftime("%Y-%m")]
        for col in BRIDGE_COLUMNS:
            sign = MOVEMENT_SIGN.get(col, 1)
            cells.append(f"{round(rec[col] * sign):,}")
        rows.append(cells)
    return markdown_table(header, rows)


def sensitivity_table(df: pd.DataFrame) -> str:
    header = [
        "Grace days",
        "Churn events",
        "Churn ARR",
        "Reactivations",
        "Reactivation ARR",
        "GRR TTM (mean)",
        "GRR TTM (latest)",
    ]
    rows = [
        [
            str(int(r.grace_days)),
            str(int(r.churned_logos)),
            f"{r.churn_arr:,.0f}",
            str(int(r.reactivated_logos)),
            f"{r.reactivation_arr:,.0f}",
            f"{r.grr_ttm_mean:.4f}",
            f"{r.grr_ttm_last:.4f}",
        ]
        for r in df.itertuples()
    ]
    return markdown_table(header, rows)


def cohort_frame(p: Pipeline, last: int = 4) -> pd.DataFrame:
    """Cohort NRR at +3/+6/+9/+12 months for the latest `last` cohorts that have a +12 reading."""
    df = p.table("cohort_nrr")
    wide = df[df["age_months"].isin(COHORT_AGES)].pivot(
        index="cohort", columns="age_months", values="nrr"
    )
    info = df[df["age_months"] == 0].set_index("cohort")[["customers", "base_arr"]]
    out = info.join(wide).dropna(subset=[COHORT_AGES[-1]]).sort_index().tail(last)
    return out


def cohort_table(p: Pipeline, last: int = 4) -> str:
    out = cohort_frame(p, last)
    header = ["Cohort", "Customers", "Base ARR", *(f"NRR +{a}m" for a in COHORT_AGES)]
    rows = [
        [
            str(cohort),
            str(int(rec["customers"])),
            f"{rec['base_arr']:,.0f}",
            *(f"{rec[a]:.4f}" for a in COHORT_AGES),
        ]
        for cohort, rec in out.iterrows()
    ]
    return markdown_table(header, rows)
