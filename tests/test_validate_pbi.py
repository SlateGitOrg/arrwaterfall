from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from arrwaterfall.export import EXPORT_SCHEMA, export_pbi
from arrwaterfall.metrics import MEASURE_NAMES
from arrwaterfall.pbi.validate import validate_project

from .conftest import PROJECT

TABLES = "ArrWaterfall.SemanticModel/definition/tables"


@pytest.fixture(scope="session")
def exported(pipe, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("pbi-data")
    export_pbi(pipe, out)
    return out


@pytest.fixture
def broken(tmp_path) -> Path:
    """A throwaway copy of the shipped project that a test may damage."""
    dest = tmp_path / "powerbi"
    shutil.copytree(PROJECT, dest, ignore=shutil.ignore_patterns("data"))
    return dest


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def test_shipped_project_passes_against_exported_csvs(exported):
    res = validate_project(PROJECT, exported)
    assert res.ok, res.problems
    assert res.notes == []
    assert sorted(res.tables) == sorted(EXPORT_SCHEMA)


def test_shipped_project_passes_against_the_export_schema_without_csvs(tmp_path):
    res = validate_project(PROJECT, tmp_path / "missing")
    assert res.ok, res.problems
    assert len(res.notes) == len(EXPORT_SCHEMA)


def test_model_measures_are_exactly_the_registry(exported):
    res = validate_project(PROJECT, exported)
    names = sorted(m for t in res.tables.values() for m in t.measures)
    assert names == sorted(MEASURE_NAMES)


def test_unknown_column_in_a_measure_is_named(broken, exported):
    edit(
        broken / TABLES / "FactArrSnapshot.tmdl",
        "FactArrSnapshot[arr_usd_spot]",
        "FactArrSnapshot[arr_usd_spotx]",
    )
    res = validate_project(broken, exported)
    assert not res.ok
    assert any("FactArrSnapshot[arr_usd_spotx]" in p and "Closing ARR" in p for p in res.problems)


def test_unknown_table_in_a_measure_is_named(broken, exported):
    edit(
        broken / TABLES / "FactArrMovement.tmdl",
        'DimMovementType[movement_type] = "new"',
        'DimMovement[movement_type] = "new"',
    )
    res = validate_project(broken, exported)
    assert any("DimMovement[movement_type]" in p and "New ARR" in p for p in res.problems)


def test_measure_outside_the_registry_is_rejected(broken, exported):
    edit(broken / TABLES / "FactArrMovement.tmdl", "measure 'New ARR'", "measure 'Brand New ARR'")
    res = validate_project(broken, exported)
    assert any("'Brand New ARR' is not in the metric registry" in p for p in res.problems)
    assert any("registry measure 'New ARR' is missing" in p for p in res.problems)


def test_column_missing_from_the_csv_header_is_named(broken, exported, tmp_path):
    data = tmp_path / "data"
    shutil.copytree(exported, data)
    csv_path = data / "DimDate.csv"
    lines = csv_path.read_text().splitlines()
    lines[0] = lines[0].replace("month_label", "month_name")
    csv_path.write_text("\n".join(lines) + "\n")
    res = validate_project(broken, data)
    assert any(
        "DimDate[month_label]" in p and "month_label" in p and "DimDate.csv" in p
        for p in res.problems
    )


def test_incompatible_column_type_is_reported(broken, exported):
    edit(
        broken / TABLES / "DimCustomer.tmdl",
        "column segment\n\t\tdataType: string",
        "column segment\n\t\tdataType: int64",
    )
    res = validate_project(broken, exported)
    assert any("DimCustomer[segment]" in p and "not compatible" in p for p in res.problems)


def test_broken_relationship_column_is_named(broken, exported):
    edit(
        broken / "ArrWaterfall.SemanticModel/definition/relationships.tmdl",
        "DimCustomer.customer_id",
        "DimCustomer.customer_key",
    )
    res = validate_project(broken, exported)
    assert any("DimCustomer.customer_key" in p for p in res.problems)


def test_unreadable_tmdl_reports_file_and_line(broken, exported):
    path = broken / TABLES / "DimDate.tmdl"
    path.write_text(path.read_text().replace("\tcolumn year", "    column year", 1))
    res = validate_project(broken, exported)
    assert any(p.startswith("tables/DimDate.tmdl:") and "tabs" in p for p in res.problems)


def test_visual_binding_to_a_missing_field_is_named(broken, exported):
    path = broken / "ArrWaterfall.Report/report.json"
    report = json.loads(path.read_text())
    visual = report["sections"][0]["visualContainers"][0]
    cfg = json.loads(visual["config"])
    cfg["singleVisual"]["prototypeQuery"]["Select"][0]["Measure"]["Property"] = "Closing ARR 2"
    visual["config"] = json.dumps(cfg)
    path.write_text(json.dumps(report))
    res = validate_project(broken, exported)
    assert any("FactArrSnapshot.Closing ARR 2" in p and "card_closing" in p for p in res.problems)


def test_missing_report_page_is_reported(broken, exported):
    path = broken / "ArrWaterfall.Report/report.json"
    report = json.loads(path.read_text())
    report["sections"] = [s for s in report["sections"] if s["displayName"] != "Cohorts"]
    path.write_text(json.dumps(report))
    res = validate_project(broken, exported)
    assert any("page 'Cohorts' is missing" in p for p in res.problems)


def test_missing_project_is_reported(tmp_path):
    res = validate_project(tmp_path / "nowhere.pbip")
    assert not res.ok


SNAPSHOT = "FactArrSnapshot.tmdl"


def strip_clear(text: str) -> str:
    return re.sub(r"REMOVEFILTERS \( DimDate \),\s*", "", text)


def test_time_shift_without_clearing_the_date_table_is_flagged(broken, exported):
    # the shipped Opening ARR clears DimDate; take that away and it returns BLANK under a
    # visual that groups by DimDate[month_label]
    path = broken / TABLES / SNAPSHOT
    path.write_text(strip_clear(path.read_text()))
    res = validate_project(broken, exported)
    assert any(
        "Opening ARR" in p and "DimDate[month_end]" in p and "REMOVEFILTERS ( DimDate )" in p
        for p in res.problems
    ), res.problems


def test_every_date_filter_in_the_snapshot_measures_is_flagged_when_not_cleared(broken, exported):
    path = broken / TABLES / SNAPSHOT
    path.write_text(strip_clear(path.read_text()))
    res = validate_project(broken, exported)
    flagged = {p.split("'")[1] for p in res.problems if "without REMOVEFILTERS" in p}
    assert flagged == {"Opening ARR", "Closing ARR", "NRR TTM", "GRR TTM"}


def test_min_of_bare_calculates_in_grr_is_flagged(broken, exported):
    path = broken / TABLES / SNAPSHOT
    path.write_text(
        path.read_text()
        .replace("COALESCE ( ", "")
        .replace(", 0 ), ", ", ")
        .replace(", 0 ) )", " ) )")
    )
    res = validate_project(broken, exported)
    assert any("GRR TTM" in p and "COALESCE" in p for p in res.problems), res.problems


@pytest.mark.parametrize(
    "expr, flagged",
    [
        ("CALCULATE ( [x], DimDate[month_end] = _p )", True),
        ("CALCULATE ( [x], ALL ( DimDate[month_end] ), DimDate[month_end] = _p )", True),
        ("CALCULATE ( [x], REMOVEFILTERS ( DimDate ), DimDate[month_end] = _p )", False),
        ("CALCULATE ( [x], ALL ( DimDate ), DimDate[month_end] = _p )", False),
        ("CALCULATE ( [x], ALLEXCEPT ( DimDate, DimDate[year] ), DimDate[month_end] = _p )", False),
        ("CALCULATE ( [x], FILTER ( ALL ( DimDate ), DimDate[month_end] = _p ) )", False),
        ('CALCULATE ( [x], DimMovementType[movement_type] = "new" )', False),
        ("CALCULATE ( [x], DimDate[month_end] <= _p, DimDate[year] = 2025 )", True),
    ],
)
def test_time_shift_rule_cases(expr, flagged):
    from arrwaterfall.pbi.validate import time_shift_problems

    assert bool(time_shift_problems(expr, {"DimDate"})) is flagged


@pytest.mark.parametrize(
    "expr, flagged",
    [
        ("MIN ( CALCULATE ( [a] ), CALCULATE ( [b] ) )", True),
        ("MAX ( COALESCE ( CALCULATE ( [a] ), 0 ), COALESCE ( CALCULATE ( [b] ), 0 ) )", False),
        ("MIN ( DimDate[month_end] )", False),
        ("MIN ( _a, CALCULATE ( [b] ) )", True),
    ],
)
def test_blank_sensitive_min_rule_cases(expr, flagged):
    from arrwaterfall.pbi.validate import blank_sensitive_min_problems

    assert bool(blank_sensitive_min_problems(expr)) is flagged
