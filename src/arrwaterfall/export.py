"""Writes the CSV tables the Power BI semantic model imports."""

from __future__ import annotations

from pathlib import Path

from .metrics import MOVEMENT_LABELS, MOVEMENT_SIGN, MOVEMENT_TYPES
from .pipeline import Pipeline
from .sensitivity import sensitivity

# Column name and TMDL data type for every exported table.  `validate-pbi` falls back to this
# schema when the data folder has no CSVs.
EXPORT_SCHEMA: dict[str, list[tuple[str, str]]] = {
    "FactArrSnapshot": [
        ("customer_id", "string"),
        ("month_end", "dateTime"),
        ("status", "string"),
        ("arr_lc", "double"),
        ("arr_usd_constant", "double"),
        ("arr_usd_spot", "double"),
    ],
    "FactArrMovement": [
        ("customer_id", "string"),
        ("month_end", "dateTime"),
        ("movement_type", "string"),
        ("amount_constant_usd", "double"),
        ("amount_spot_usd", "double"),
    ],
    "DimCustomer": [
        ("customer_id", "string"),
        ("segment", "string"),
        ("currency", "string"),
        ("first_contract_date", "dateTime"),
        ("cohort", "string"),
    ],
    "DimDate": [
        ("month_end", "dateTime"),
        ("month_idx", "int64"),
        ("year", "int64"),
        ("quarter", "string"),
        ("month_label", "string"),
    ],
    "DimMovementType": [
        ("movement_type", "string"),
        ("label", "string"),
        ("sort_order", "int64"),
        ("sign", "int64"),
    ],
    "FactGraceSensitivity": [
        ("grace_days", "int64"),
        ("churned_logos", "int64"),
        ("churn_arr", "double"),
        ("reactivated_logos", "int64"),
        ("reactivation_arr", "double"),
        ("grr_ttm_last", "double"),
        ("grr_ttm_mean", "double"),
        ("nrr_ttm_last", "double"),
    ],
}


def _movement_sql() -> str:
    parts = []
    for t in MOVEMENT_TYPES:
        parts.append(
            f"SELECT c.customer_id, c.month_end, '{t}' AS movement_type, "
            f"ROUND(c.{t}, 4) AS amount_constant_usd, ROUND(s.{t}, 4) AS amount_spot_usd "
            "FROM (SELECT * FROM customer_bridge WHERE view = 'constant') c "
            "JOIN (SELECT * FROM customer_bridge WHERE view = 'spot') s "
            "USING (customer_id, month_idx) "
            f"WHERE c.{t} <> 0 OR s.{t} <> 0"
        )
    return " UNION ALL ".join(parts)


def export_pbi(p: Pipeline, out_dir: str | Path) -> dict[str, int]:
    """Write one CSV per model table into `out_dir` and return the row counts."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    con = p.con

    queries = {
        "FactArrSnapshot": (
            "SELECT customer_id, month_end, status, arr_lc, ROUND(arr_usd_constant, 4) AS "
            "arr_usd_constant, ROUND(arr_usd_spot, 4) AS arr_usd_spot FROM customer_arr "
            "WHERE arr_lc > 0 ORDER BY customer_id, month_end"
        ),
        "FactArrMovement": (
            f"SELECT * FROM ({_movement_sql()}) ORDER BY customer_id, month_end, movement_type"
        ),
        "DimCustomer": (
            "SELECT customer_id, segment, currency, first_contract_date, "
            "CAST(year(first_contract_date) AS VARCHAR) || '-Q' || "
            "CAST(quarter(first_contract_date) AS VARCHAR) AS cohort FROM customers "
            "ORDER BY customer_id"
        ),
        "DimDate": (
            "SELECT month_end, month_idx, CAST(year(month_end) AS BIGINT) AS year, "
            "CAST(year(month_end) AS VARCHAR) || '-Q' || CAST(quarter(month_end) AS VARCHAR) "
            "AS quarter, strftime(month_end, '%Y-%m') AS month_label FROM months ORDER BY month_idx"
        ),
        "DimMovementType": (
            "SELECT * FROM (VALUES "
            + ", ".join(
                f"('{t}', '{MOVEMENT_LABELS[t]}', {i}, {MOVEMENT_SIGN[t]})"
                for i, t in enumerate(MOVEMENT_TYPES, start=1)
            )
            + ") AS v(movement_type, label, sort_order, sign)"
        ),
    }
    counts: dict[str, int] = {}
    for table, sql in queries.items():
        target = str(out / f"{table}.csv").replace("'", "''")
        con.execute(f"COPY ({sql}) TO '{target}' (HEADER, DELIMITER ',')")
        counts[table] = con.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]

    sens = sensitivity(p.data_dir)
    sens.to_csv(out / "FactGraceSensitivity.csv", index=False, float_format="%.6f")
    counts["FactGraceSensitivity"] = len(sens)
    return counts
