"""Command line interface: generate, build, bridge, sensitivity, export-pbi, validate-pbi."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from .checks import run_checks
from .export import export_pbi
from .metrics import DEFAULT_GRACE_DAYS
from .pbi.validate import validate_project
from .pipeline import InputError, run_pipeline
from .reporting import bridge_table, cohort_table, sensitivity_table
from .sensitivity import sensitivity as run_sensitivity
from .synth import generate as generate_data

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)

DataOpt = Annotated[Path, typer.Option("--data", help="Folder with the three input CSVs.")]
GraceOpt = Annotated[
    int, typer.Option("--grace-days", min=0, help="Days a lapsed customer is carried as in_grace.")
]


def _pipeline(data: Path, grace_days: int, database: str = ":memory:"):
    try:
        return run_pipeline(data, grace_days=grace_days, database=database)
    except InputError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e


@app.command()
def generate(
    out: Annotated[Path, typer.Option(help="Output folder.")] = Path("data"),
    seed: Annotated[int, typer.Option(help="Seed of the synthetic book.")] = 11,
    customers: Annotated[int, typer.Option(min=400, help="Number of customers.")] = 900,
) -> None:
    """Write customers.csv, contract_lines.csv, fx_rates.csv and truth.json."""
    truth = generate_data(out, seed=seed, n_customers=customers)
    typer.echo(
        f"wrote {truth['n_customers']} customers and {truth['n_lines']} contract lines to {out}/ "
        f"({len(truth['late_renewals'])} late renewals, {len(truth['true_churns'])} true churns, "
        f"{len(truth['reactivations'])} reactivations, "
        f"{len(truth['overlap_renewals'])} overlap renewals)"
    )


@app.command()
def build(
    data: DataOpt = Path("data"),
    out: Annotated[Path, typer.Option(help="Output folder.")] = Path("out"),
    grace_days: GraceOpt = DEFAULT_GRACE_DAYS,
) -> None:
    """Run the SQL pipeline, check the bridge, and write the derived tables."""
    out.mkdir(parents=True, exist_ok=True)
    db = out / "arrwaterfall.duckdb"
    db.unlink(missing_ok=True)
    with _pipeline(data, grace_days, str(db)) as p:
        for name in ("customer_arr", "bridge", "monthly_metrics", "cohort_nrr", "churn_event"):
            target = str(out / f"{name}.csv").replace("'", "''")
            p.con.execute(f"COPY {name} TO '{target}' (HEADER, DELIMITER ',')")
        failed = False
        for r in run_checks(p):
            typer.echo(f"[{'ok' if r.passed else 'FAIL'}] {r.name}: {r.detail}")
            failed |= not r.passed
        typer.echo(f"grace window {grace_days} days; tables written to {out}/")
    if failed:
        raise typer.Exit(1)


@app.command()
def bridge(
    data: DataOpt = Path("data"),
    view: Annotated[str, typer.Option(help="'spot' or 'constant'.")] = "spot",
    months: Annotated[int, typer.Option(min=1, help="Number of latest months to show.")] = 12,
    grace_days: GraceOpt = DEFAULT_GRACE_DAYS,
    cohorts: Annotated[bool, typer.Option(help="Print the cohort NRR table instead.")] = False,
) -> None:
    """Print the monthly ARR bridge (USD) or, with --cohorts, cohort NRR."""
    if view not in ("spot", "constant"):
        typer.echo("error: --view must be 'spot' or 'constant'", err=True)
        raise typer.Exit(2)
    with _pipeline(data, grace_days) as p:
        typer.echo(cohort_table(p) if cohorts else bridge_table(p, view, months))


@app.command()
def sensitivity(data: DataOpt = Path("data")) -> None:
    """Churn, reactivation and GRR under grace windows of 0, 30, 60 and 90 days."""
    try:
        typer.echo(sensitivity_table(run_sensitivity(data)))
    except InputError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e


@app.command("export-pbi")
def export_pbi_cmd(
    data: DataOpt = Path("data"),
    out: Annotated[Path, typer.Option(help="Folder the Power BI model reads.")] = Path(
        "powerbi/data"
    ),
    grace_days: GraceOpt = DEFAULT_GRACE_DAYS,
) -> None:
    """Write the CSV tables of the Power BI semantic model."""
    with _pipeline(data, grace_days) as p:
        counts = export_pbi(p, out)
    for table, n in counts.items():
        typer.echo(f"{table}.csv: {n} rows")


@app.command("validate-pbi")
def validate_pbi(
    project: Annotated[Path, typer.Option(help="The .pbip file or its folder.")] = Path("powerbi"),
    data_dir: Annotated[Path, typer.Option(help="Folder with the exported CSVs.")] = Path(
        "powerbi/data"
    ),
) -> None:
    """Validate the TMDL model, measure names, CSV schema and report bindings."""
    result = validate_project(project, data_dir)
    for note in result.notes:
        typer.echo(f"note: {note}")
    if result.ok:
        measures = sum(len(t.measures) for t in result.tables.values())
        typer.echo(f"OK: {len(result.tables)} tables, {measures} measures, all references resolve")
        return
    for problem in result.problems:
        typer.echo(f"FAIL: {problem}", err=True)
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
