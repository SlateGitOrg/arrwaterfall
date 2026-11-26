"""Runs the DuckDB SQL pipeline in `sql/` over the three input CSVs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

from .metrics import DEFAULT_GRACE_DAYS, MEASURES, TTM_MONTHS

SQL_DIR = Path(__file__).parent / "sql"

INPUT_FILES = {
    "customers": (
        "customers.csv",
        "{'customer_id': 'VARCHAR', 'segment': 'VARCHAR', 'currency': 'VARCHAR', "
        "'first_contract_date': 'DATE'}",
    ),
    "contract_lines": (
        "contract_lines.csv",
        "{'line_id': 'VARCHAR', 'customer_id': 'VARCHAR', 'product': 'VARCHAR', "
        "'start_date': 'DATE', 'end_date': 'DATE', 'annualised_amount': 'DOUBLE', "
        "'replaces_line_id': 'VARCHAR'}",
    ),
    "fx_rates": (
        "fx_rates.csv",
        "{'month_end': 'DATE', 'currency': 'VARCHAR', 'spot_usd': 'DOUBLE', 'plan_usd': 'DOUBLE'}",
    ),
}


class InputError(ValueError):
    """The input CSVs break a rule the pipeline relies on."""


def sql_files() -> list[Path]:
    return sorted(SQL_DIR.glob("*.sql"))


def render(sql: str, grace_days: int, ttm_months: int) -> str:
    if grace_days < 0 or ttm_months < 1:
        raise ValueError("grace_days must be >= 0 and ttm_months >= 1")
    return sql.replace("$grace_days", str(int(grace_days))).replace(
        "$ttm_months", str(int(ttm_months))
    )


def _load_inputs(con: duckdb.DuckDBPyConnection, data_dir: Path) -> None:
    for table, (filename, columns) in INPUT_FILES.items():
        path = data_dir / filename
        if not path.is_file():
            raise InputError(f"missing input file: {path}")
        quoted = str(path).replace("'", "''")
        con.execute(
            f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM "
            f"read_csv('{quoted}', header = true, columns = {columns})"
        )
    con.execute("UPDATE contract_lines SET replaces_line_id = NULL WHERE replaces_line_id = ''")


def _check_inputs(con: duckdb.DuckDBPyConnection) -> None:
    checks = {
        "contract lines whose end_date is before start_date": (
            "SELECT COUNT(*) FROM contract_lines WHERE end_date < start_date"
        ),
        "contract lines of unknown customers": (
            "SELECT COUNT(*) FROM contract_lines WHERE customer_id NOT IN "
            "(SELECT customer_id FROM customers)"
        ),
        "replaces_line_id values that name no line": (
            "SELECT COUNT(*) FROM contract_lines WHERE replaces_line_id IS NOT NULL AND "
            "replaces_line_id NOT IN (SELECT line_id FROM contract_lines)"
        ),
        "duplicate line ids": (
            "SELECT COUNT(*) FROM (SELECT line_id FROM contract_lines GROUP BY line_id "
            "HAVING COUNT(*) > 1)"
        ),
        "currencies with a plan rate that changes over time": (
            "SELECT COUNT(*) FROM (SELECT currency FROM fx_rates GROUP BY currency "
            "HAVING COUNT(DISTINCT plan_usd) > 1)"
        ),
        "customer currencies without a rate in every month": (
            "SELECT COUNT(*) FROM (SELECT c.currency FROM customers c LEFT JOIN fx_rates r "
            "USING (currency) GROUP BY c.currency "
            "HAVING COUNT(DISTINCT r.month_end) < (SELECT COUNT(DISTINCT month_end) FROM fx_rates))"
        ),
    }
    problems = [
        f"{label}: {n}" for label, sql in checks.items() if (n := con.execute(sql).fetchone()[0])
    ]
    if problems:
        raise InputError("invalid input data: " + "; ".join(problems))


@dataclass
class Pipeline:
    """The finished pipeline: a DuckDB connection holding every derived table."""

    con: duckdb.DuckDBPyConnection
    data_dir: Path
    grace_days: int
    ttm_months: int

    def table(self, name: str, order_by: str | None = None) -> pd.DataFrame:
        sql = f"SELECT * FROM {name}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        return self.con.execute(sql).df()

    def query(self, sql: str) -> pd.DataFrame:
        return self.con.execute(sql).df()

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> Pipeline:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def verify_registry(con: duckdb.DuckDBPyConnection) -> None:
    """Every registered measure must point at a column the pipeline actually produced."""
    for measure in MEASURES:
        cols = {r[0] for r in con.execute(f"DESCRIBE {measure.table}").fetchall()}
        if measure.column not in cols:
            raise RuntimeError(
                f"registry measure {measure.name!r} needs {measure.table}.{measure.column}"
            )


def run_pipeline(
    data_dir: str | Path,
    grace_days: int = DEFAULT_GRACE_DAYS,
    ttm_months: int = TTM_MONTHS,
    database: str = ":memory:",
) -> Pipeline:
    data_dir = Path(data_dir)
    con = duckdb.connect(database)
    try:
        _load_inputs(con, data_dir)
        _check_inputs(con)
        for path in sql_files():
            con.execute(render(path.read_text(), grace_days, ttm_months))
        verify_registry(con)
    except Exception:
        con.close()
        raise
    return Pipeline(con, data_dir, grace_days, ttm_months)
