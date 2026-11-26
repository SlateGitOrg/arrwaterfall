from __future__ import annotations

import csv
import shutil

import pytest
from typer.testing import CliRunner

from arrwaterfall.cli import app
from arrwaterfall.export import EXPORT_SCHEMA, export_pbi
from arrwaterfall.metrics import MOVEMENT_TYPES
from arrwaterfall.pipeline import InputError, run_pipeline
from arrwaterfall.reporting import bridge_table, cohort_frame, sensitivity_table
from arrwaterfall.sensitivity import sensitivity

from .conftest import PROJECT

runner = CliRunner()


@pytest.fixture(scope="module")
def exported(pipe, tmp_path_factory):
    out = tmp_path_factory.mktemp("export")
    counts = export_pbi(pipe, out)
    return out, counts


def test_export_writes_every_table_with_the_declared_header(exported):
    out, counts = exported
    assert set(counts) == set(EXPORT_SCHEMA)
    for table, cols in EXPORT_SCHEMA.items():
        with open(out / f"{table}.csv", newline="") as fh:
            reader = csv.reader(fh)
            assert next(reader) == [c for c, _ in cols]
            assert sum(1 for _ in reader) == counts[table]


def test_export_row_counts_and_dimensions(exported):
    _, counts = exported
    assert counts["DimCustomer"] == 900
    assert counts["DimDate"] == 48
    assert counts["DimMovementType"] == len(MOVEMENT_TYPES) == 6
    assert counts["FactGraceSensitivity"] == 4


def test_exported_movements_add_up_to_the_bridge(pipe, exported):
    out, _ = exported
    totals: dict[str, float] = {}
    with open(out / "FactArrMovement.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            totals[r["movement_type"]] = totals.get(r["movement_type"], 0.0) + float(
                r["amount_spot_usd"]
            )
    bridge = pipe.query("SELECT * FROM bridge WHERE view = 'spot'")
    for t in MOVEMENT_TYPES:
        assert totals[t] == pytest.approx(bridge[t].sum(), abs=5.0)


def test_snapshot_only_holds_customer_months_with_arr(exported):
    out, _ = exported
    with open(out / "FactArrSnapshot.csv", newline="") as fh:
        assert all(float(r["arr_lc"]) > 0 for r in csv.DictReader(fh))


def test_sensitivity_table_values(data_dir, truth):
    df = sensitivity(data_dir)
    assert list(df["grace_days"]) == [0, 30, 60, 90]
    assert list(df["churned_logos"]) == [
        15 + sum(1 for e in truth["late_renewals"] if e["gap_days"] > g) for g in (0, 30, 60, 90)
    ]
    assert df["churn_arr"].is_monotonic_decreasing
    assert df["churn_arr"].iloc[0] > df["churn_arr"].iloc[-1] * 3
    text = sensitivity_table(df)
    assert text.splitlines()[0].startswith("| Grace days")
    assert len(text.splitlines()) == 6


def test_bridge_table_prints_signed_columns(pipe):
    lines = bridge_table(pipe, "spot", 3).splitlines()
    assert len(lines) == 5
    assert "FX" in lines[0] and "Closing" in lines[0]
    assert " -0 " not in "".join(lines)


def test_cohort_frame_has_four_cohorts_with_a_twelve_month_reading(pipe):
    df = cohort_frame(pipe)
    assert len(df) == 4
    assert df[12].notna().all()


def test_cli_generate_build_bridge_export_validate(tmp_path):
    data, out, pbi = tmp_path / "d", tmp_path / "o", tmp_path / "p"
    res = runner.invoke(app, ["generate", "--out", str(data), "--seed", "5"])
    assert res.exit_code == 0 and "900 customers" in res.output

    res = runner.invoke(app, ["build", "--data", str(data), "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert res.output.count("[ok]") == 5 and "FAIL" not in res.output
    assert (out / "bridge.csv").is_file() and (out / "cohort_nrr.csv").is_file()

    res = runner.invoke(app, ["bridge", "--data", str(data), "--months", "2", "--view", "constant"])
    assert res.exit_code == 0 and len(res.output.strip().splitlines()) == 4

    res = runner.invoke(app, ["bridge", "--data", str(data), "--cohorts"])
    assert res.exit_code == 0 and "NRR +12m" in res.output

    res = runner.invoke(app, ["export-pbi", "--data", str(data), "--out", str(pbi)])
    assert res.exit_code == 0 and "DimCustomer.csv: 900 rows" in res.output

    res = runner.invoke(app, ["validate-pbi", "--project", str(PROJECT), "--data-dir", str(pbi)])
    assert res.exit_code == 0 and res.output.startswith("OK:")


def test_cli_validate_pbi_exits_1_and_names_the_bad_reference(tmp_path):
    broken = tmp_path / "powerbi"
    shutil.copytree(PROJECT, broken, ignore=shutil.ignore_patterns("data"))
    path = broken / "ArrWaterfall.SemanticModel/definition/tables/FactArrMovement.tmdl"
    path.write_text(path.read_text().replace("amount_spot_usd", "amount_spot_eur", 1))
    res = runner.invoke(
        app, ["validate-pbi", "--project", str(broken), "--data-dir", str(tmp_path / "none")]
    )
    assert res.exit_code == 1
    # the column was renamed in the model, so the measures that sum it no longer resolve
    assert "unresolved reference FactArrMovement[amount_spot_usd]" in res.output


def test_cli_rejects_a_bad_view_and_missing_inputs(tmp_path):
    assert runner.invoke(app, ["bridge", "--data", str(tmp_path), "--view", "weird"]).exit_code == 2
    res = runner.invoke(
        app, ["build", "--data", str(tmp_path / "nothing"), "--out", str(tmp_path / "o")]
    )
    assert res.exit_code == 2 and "missing input file" in res.output


def test_invalid_inputs_are_refused(tmp_path):
    shutil.copytree(__import__("pathlib").Path(__file__).parent / "fixtures", tmp_path / "f")
    path = tmp_path / "f" / "contract_lines.csv"
    path.write_text(path.read_text().replace("2025-03-31,12000", "2023-03-31,12000"))
    with pytest.raises(InputError, match="end_date is before start_date"):
        run_pipeline(tmp_path / "f")
