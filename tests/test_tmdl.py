from __future__ import annotations

import pytest

from arrwaterfall.pbi.tmdl import TmdlError, parse_tmdl
from arrwaterfall.pbi.validate import dax_references

T = "\t"

SAMPLE = (
    "table Sales\n"
    f"{T}lineageTag: abc\n\n"
    f"{T}column amount\n{T}{T}dataType: double\n{T}{T}sourceColumn: amount\n\n"
    f"{T}measure 'Total Sales' =\n{T * 3}SUM ( Sales[amount] )\n{T * 3}+ 0\n"
    f"{T}{T}formatString: 0\n\n"
    f"{T}partition Sales = m\n{T}{T}mode: import\n{T}{T}source =\n"
    f"{T * 4}let\n{T * 5}S = 1\n{T * 4}in\n{T * 5}S\n"
)


def test_parses_tables_columns_measures_and_partitions():
    (table,) = parse_tmdl(SAMPLE)
    assert table.kind == "table" and table.name == "Sales"
    (col,) = table.of_kind("column")
    assert col.props == {"dataType": "double", "sourceColumn": "amount"}
    (measure,) = table.of_kind("measure")
    assert measure.name == "Total Sales"
    assert measure.expression == "SUM ( Sales[amount] )\n+ 0"
    assert measure.props["formatString"] == "0"
    (part,) = table.of_kind("partition")
    assert part.expression == "m" and part.props["source"] == "let\nS = 1\nin\nS"


def test_inline_measure_expression_and_flags():
    nodes = parse_tmdl(f"model Model\n{T}culture: en-US\n{T}discourageImplicitMeasures\n")
    assert nodes[0].props == {"culture": "en-US", "discourageImplicitMeasures": "true"}
    (tbl,) = parse_tmdl(f"table T\n{T}measure M = 1 + 1\n{T}{T}formatString: 0\n")
    assert tbl.of_kind("measure")[0].expression == "1 + 1"


def test_quoted_names_with_escaped_quotes():
    (tbl,) = parse_tmdl(f"table 'Sales ''EU'''\n{T}column x\n{T}{T}dataType: string\n")
    assert tbl.name == "Sales 'EU'"


def test_spaces_instead_of_tabs_are_rejected_with_a_line_number():
    with pytest.raises(TmdlError) as err:
        parse_tmdl("table T\n    column x\n", file="t.tmdl")
    assert "t.tmdl:2" in str(err.value) and "tabs" in str(err.value)


def test_indentation_jump_is_rejected():
    with pytest.raises(TmdlError, match="unexpected indentation"):
        parse_tmdl(f"table T\n{T}{T}column x\n")


def test_unknown_line_is_rejected():
    with pytest.raises(TmdlError, match="unrecognised line"):
        parse_tmdl(f"table T\n{T}frobnicate x\n")


def test_dax_reference_extraction_ignores_strings():
    refs = dax_references("CALCULATE ( SUM ( 'Dim Date'[year] ), T[kind] = \"A[b]\" ) + [Other]")
    assert refs == [("Dim Date", "year"), ("T", "kind"), (None, "Other")]
