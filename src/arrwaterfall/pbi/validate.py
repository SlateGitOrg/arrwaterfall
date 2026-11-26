"""Static validation of a PBIP project against the metric registry and the exported CSVs.

Checks, in order: project files resolve; every TMDL file parses; the model is internally
consistent (relationships, partitions); every column and measure reference in a DAX measure
resolves; time-shifting CALCULATE calls clear the date table and MIN/MAX terms are blank-safe;
TMDL columns exist in the CSV headers with compatible types; measure names match the registry
in `arrwaterfall.metrics`; every report visual binds to a column or measure of the model.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..export import EXPORT_SCHEMA
from ..metrics import MEASURE_NAMES
from .tmdl import Node, TmdlError, parse_file

EXPECTED_PAGES = ("Bridge", "Cohorts", "Definition sensitivity")
DATA_TYPES = {"string", "int64", "double", "decimal", "dateTime", "boolean"}
_DAX_REF = re.compile(r"(?:(?P<t>'(?:[^']|'')+'|[A-Za-z_]\w*)\s*)?\[(?P<c>[^\]]+)\]")


@dataclass
class Table:
    name: str
    columns: dict[str, str] = field(default_factory=dict)  # name -> dataType
    source_columns: dict[str, str] = field(default_factory=dict)  # name -> sourceColumn
    measures: dict[str, str] = field(default_factory=dict)  # name -> DAX
    csv: str | None = None


@dataclass
class Result:
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    tables: dict[str, Table] = field(default_factory=dict)
    date_tables: set[str] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return not self.problems


def _strip_dax(expr: str) -> str:
    expr = re.sub(r'"(?:[^"]|"")*"', '""', expr)
    return re.sub(r"//[^\n]*", "", expr)


def dax_references(expr: str) -> list[tuple[str | None, str]]:
    """(table, name) pairs for every `Table[Name]` / `[Name]` in a DAX expression."""
    refs = []
    for m in _DAX_REF.finditer(_strip_dax(expr)):
        t = m.group("t")
        if t and t.startswith("'"):
            t = t[1:-1].replace("''", "'")
        refs.append((t, m.group("c")))
    return refs


def dax_calls(expr: str, function: str) -> list[list[str]]:
    """Top-level argument texts of every call to `function` in a DAX expression."""
    text = _strip_dax(expr)
    calls = []
    for m in re.finditer(rf"\b{function}\s*\(", text, flags=re.IGNORECASE):
        depth, args, start = 1, [], m.end()
        i = start
        while i < len(text) and depth:
            ch = text[i]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
                if depth == 0:
                    break
            elif ch == "," and depth == 1:
                args.append(text[start:i])
                start = i + 1
            i += 1
        args.append(text[start:i])
        calls.append([a.strip() for a in args])
    return calls


def _table_pattern(table: str) -> str:
    return rf"(?:'{re.escape(table)}'|{re.escape(table)})"


def time_shift_problems(expr: str, date_tables: set[str]) -> list[str]:
    """CALCULATE calls that filter a date column but leave the date table's other filters alone.

    A visual that groups by DimDate[month_label] or DimDate[year] puts a filter on that column.
    `CALCULATE(..., DimDate[month_end] = EOMONTH(_m, -1))` only replaces the month_end filter, so
    the shifted month is intersected with the visual's own month_label / year filter and the
    result is BLANK.  The call must clear the whole date table (REMOVEFILTERS / ALL of the table).
    """
    problems = []
    for func in ("CALCULATE", "CALCULATETABLE"):
        for args in dax_calls(expr, func):
            for table in sorted(date_tables):
                tp = _table_pattern(table)
                column_filter = re.compile(rf"^{tp}\[([^\]]+)\]\s*(?:<>|<=|>=|=|<|>|\bIN\b)", re.I)
                cleared = re.compile(
                    rf"^(?:REMOVEFILTERS|ALL)\s*\(\s*{tp}\s*\)|^ALLEXCEPT\s*\(\s*{tp}\s*,", re.I
                )
                filtered = [m.group(1) for a in args[1:] if (m := column_filter.match(a))]
                if filtered and not any(cleared.match(a) for a in args[1:]):
                    problems.append(
                        f"CALCULATE filters {table}[{filtered[0]}] without "
                        f"REMOVEFILTERS ( {table} ) or ALL ( {table} ): the visual's other "
                        f"{table} filters would intersect and the measure returns BLANK"
                    )
    return problems


def blank_sensitive_min_problems(expr: str) -> list[str]:
    """MIN/MAX of two CALCULATE results: a BLANK side is ignored, so churned ARR looks retained."""
    problems = []
    for func in ("MIN", "MAX"):
        for args in dax_calls(expr, func):
            if len(args) >= 2 and any(re.match(r"CALCULATE\s*\(", a, re.I) for a in args):
                problems.append(
                    f"{func} over a bare CALCULATE returns the other argument when one side is "
                    "BLANK; wrap each term in COALESCE ( ..., 0 )"
                )
    return problems


def _unquote(name: str) -> str:
    return name[1:-1] if name.startswith("'") and name.endswith("'") else name


def _split_column_ref(ref: str) -> tuple[str, str]:
    table, _, column = ref.rpartition(".")
    return _unquote(table), _unquote(column)


def _infer_csv_type(values: list[str]) -> str:
    kinds = set()
    for v in values:
        if v == "":
            continue
        if re.fullmatch(r"-?\d+", v):
            kinds.add("int")
        elif re.fullmatch(r"-?\d+\.\d*(?:[eE][-+]?\d+)?|-?\d+[eE][-+]?\d+", v):
            kinds.add("float")
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) and _is_date(v):
            kinds.add("date")
        elif v.lower() in {"true", "false"}:
            kinds.add("bool")
        else:
            kinds.add("string")
    if not kinds:
        return "empty"
    if kinds <= {"int"}:
        return "int"
    if kinds <= {"int", "float"}:
        return "float"
    return kinds.pop() if len(kinds) == 1 else "string"


def _is_date(v: str) -> bool:
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


_COMPATIBLE = {
    "string": {"int", "float", "date", "bool", "string", "empty"},
    "int64": {"int", "empty"},
    "double": {"int", "float", "empty"},
    "decimal": {"int", "float", "empty"},
    "dateTime": {"date", "empty"},
    "boolean": {"bool", "empty"},
}
_SCHEMA_KIND = {"string": "string", "int64": "int", "double": "float", "dateTime": "date"}


def _read_csv_types(path: Path) -> tuple[list[str], dict[str, str]]:
    with open(path, newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, [])
        cols: dict[str, list[str]] = {h: [] for h in header}
        for row in reader:
            for h, v in zip(header, row, strict=False):
                cols[h].append(v)
    return header, {h: _infer_csv_type(v) for h, v in cols.items()}


def _load_model(model_dir: Path, res: Result) -> dict[str, Node]:
    """Parse every TMDL file; returns the nodes of each file by relative name."""
    files: dict[str, Node] = {}
    parsed: dict[str, list[Node]] = {}
    paths = [
        p
        for p in [
            model_dir / "model.tmdl",
            model_dir / "database.tmdl",
            model_dir / "expressions.tmdl",
            model_dir / "relationships.tmdl",
        ]
        if p.is_file()
    ]
    paths += sorted((model_dir / "tables").glob("*.tmdl"))
    for required in ("model.tmdl", "relationships.tmdl", "expressions.tmdl"):
        if not (model_dir / required).is_file():
            res.problems.append(f"{required}: file is missing")
    if not (model_dir / "tables").is_dir():
        res.problems.append("tables/: folder is missing")
    for path in paths:
        rel = str(path.relative_to(model_dir))
        try:
            parsed[rel] = parse_file(path)
        except TmdlError as e:
            res.problems.append(f"{rel}:{e.line}: TMDL parse error: {e.message}")
    for rel, nodes in parsed.items():
        for n in nodes:
            files[f"{rel}#{n.kind}:{n.name}:{n.line}"] = n
    return files


def _build_tables(nodes: dict[str, Node], res: Result) -> None:
    for node in nodes.values():
        if node.kind != "table":
            continue
        t = Table(node.name)
        if node.name in res.tables:
            res.problems.append(f"{node.file}:{node.line}: duplicate table {node.name!r}")
        for col in node.of_kind("column"):
            where = f"{node.file}:{col.line}"
            if col.name in t.columns:
                res.problems.append(f"{where}: duplicate column {node.name}[{col.name}]")
            dtype = col.props.get("dataType")
            if dtype not in DATA_TYPES:
                res.problems.append(
                    f"{where}: column {node.name}[{col.name}] has dataType {dtype!r}"
                )
            t.columns[col.name] = dtype or ""
            t.source_columns[col.name] = col.props.get("sourceColumn", col.name)
        for ms in node.of_kind("measure"):
            where = f"{node.file}:{ms.line}"
            if not ms.expression:
                res.problems.append(f"{where}: measure {ms.name!r} has no expression")
            if ms.name in t.measures:
                res.problems.append(f"{where}: duplicate measure {ms.name!r}")
            t.measures[ms.name] = ms.expression or ""
        for part in node.of_kind("partition"):
            source = part.props.get("source", "")
            m = re.search(r'DataFolder\s*&\s*"[\\/]([^"\\/]+\.csv)"', source)
            if not m:
                res.problems.append(
                    f"{node.file}:{part.line}: partition {part.name!r} "
                    "does not read a CSV from DataFolder"
                )
            else:
                t.csv = m.group(1)
        res.tables[node.name] = t


def _all_measures(res: Result) -> dict[str, str]:
    out: dict[str, str] = {}
    for t in res.tables.values():
        for m in t.measures:
            if m in out:
                res.problems.append(f"measure {m!r} is defined in both {out[m]} and {t.name}")
            out[m] = t.name
    return out


def _check_model_files(nodes: dict[str, Node], res: Result) -> None:
    refs = {
        n.name.split(" ", 1)[1]
        for n in nodes.values()
        if n.kind == "model"
        for n in n.of_kind("ref")
        if n.name.startswith("table ")
    }
    for ref in sorted(refs - set(res.tables)):
        res.problems.append(f"model.tmdl: ref table {ref} has no table file")
    for name in sorted(set(res.tables) - refs):
        res.problems.append(f"model.tmdl: table {name} is not referenced")
    expressions = {n.name for n in nodes.values() if n.kind == "expression"}
    if "DataFolder" not in expressions:
        res.problems.append("expressions.tmdl: parameter DataFolder is not defined")
    for n in nodes.values():
        if n.kind != "relationship":
            continue
        where = f"relationships.tmdl:{n.line}"
        for side in ("fromColumn", "toColumn"):
            ref = n.props.get(side)
            if not ref:
                res.problems.append(f"{where}: relationship is missing {side}")
                continue
            table, column = _split_column_ref(ref)
            if table not in res.tables or column not in res.tables[table].columns:
                res.problems.append(f"{where}: {side} {ref} does not resolve to a column")
            elif side == "toColumn" and res.tables[table].columns[column] == "dateTime":
                res.date_tables.add(table)


def _check_dax(res: Result) -> None:
    measures = _all_measures(res)
    for t in res.tables.values():
        for mname, expr in t.measures.items():
            for table, name in dax_references(expr):
                label = f"measure {mname!r}"
                if table is None:
                    if name not in measures and name not in t.columns:
                        res.problems.append(f"{label}: unresolved reference [{name}]")
                elif table not in res.tables:
                    res.problems.append(
                        f"{label}: unresolved reference {table}[{name}] (no table {table!r})"
                    )
                elif (
                    name not in res.tables[table].columns and name not in res.tables[table].measures
                ):
                    res.problems.append(f"{label}: unresolved reference {table}[{name}]")


def _check_dax_semantics(res: Result) -> None:
    for t in res.tables.values():
        for mname, expr in t.measures.items():
            found = time_shift_problems(expr, res.date_tables) + blank_sensitive_min_problems(expr)
            res.problems.extend(f"measure {mname!r}: {msg}" for msg in found)


def _check_registry(res: Result) -> None:
    in_model = set(_all_measures(res))
    for name in sorted(in_model - set(MEASURE_NAMES)):
        res.problems.append(
            f"measure {name!r} is not in the metric registry (arrwaterfall/metrics.py)"
        )
    for name in sorted(set(MEASURE_NAMES) - in_model):
        res.problems.append(f"registry measure {name!r} is missing from the semantic model")


def _check_data(res: Result, data_dir: Path | None) -> None:
    for t in res.tables.values():
        csv_name = t.csv or f"{t.name}.csv"
        path = data_dir / csv_name if data_dir else None
        if path is not None and path.is_file():
            header, types = _read_csv_types(path)
            source = f"{csv_name}"
        elif t.name in EXPORT_SCHEMA:
            header = [c for c, _ in EXPORT_SCHEMA[t.name]]
            types = {c: _SCHEMA_KIND[d] for c, d in EXPORT_SCHEMA[t.name]}
            source = f"export schema for {t.name} (no {csv_name} in the data folder)"
            res.notes.append(f"{t.name}: validated against the export schema, {csv_name} not found")
        else:
            res.problems.append(f"{t.name}: no CSV {csv_name} and no export schema")
            continue
        for col, dtype in t.columns.items():
            src = t.source_columns[col]
            if src not in header:
                res.problems.append(
                    f"{t.name}[{col}]: sourceColumn {src!r} is not a column of {source}"
                )
            elif dtype in _COMPATIBLE and types[src] not in _COMPATIBLE[dtype]:
                res.problems.append(
                    f"{t.name}[{col}]: dataType {dtype} is not compatible with {source} "
                    f"column {src!r} holding {types[src]} values"
                )


def _check_report(report_dir: Path, res: Result) -> None:
    path = report_dir / "report.json"
    if not path.is_file():
        res.problems.append(f"{report_dir.name}/report.json: file is missing")
        return
    try:
        report = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        res.problems.append(f"report.json: invalid JSON ({e})")
        return
    sections = report.get("sections", [])
    names = [s.get("displayName") for s in sections]
    for page in EXPECTED_PAGES:
        if page not in names:
            res.problems.append(f"report.json: page {page!r} is missing")
    for section in sections:
        page = section.get("displayName", "?")
        for vc in section.get("visualContainers", []):
            try:
                cfg = json.loads(vc["config"])
                single = cfg["singleVisual"]
                vname = cfg.get("name", "?")
            except (KeyError, TypeError, json.JSONDecodeError):
                res.problems.append(
                    f"report.json: page {page!r} has a visual with an unreadable config"
                )
                continue
            where = f"report.json: page {page!r} visual {vname!r}"
            query = single.get("prototypeQuery", {})
            alias = {f["Name"]: f["Entity"] for f in query.get("From", [])}
            names_seen = set()
            for item in query.get("Select", []):
                names_seen.add(item.get("Name"))
                kind = next((k for k in ("Column", "Measure", "Aggregation") if k in item), None)
                if kind is None:
                    res.problems.append(f"{where}: select {item.get('Name')!r} has no field")
                    continue
                body = item[kind]
                if kind == "Aggregation":
                    body = body["Expression"]["Column"]
                    kind = "Column"
                source = body["Expression"]["SourceRef"]["Source"]
                prop = body["Property"]
                entity = alias.get(source)
                label = f"{entity}.{prop}"
                if entity not in res.tables:
                    res.problems.append(f"{where}: binding {label} names no table {entity!r}")
                    continue
                table = res.tables[entity]
                pool = table.columns if kind == "Column" else table.measures
                if prop not in pool:
                    res.problems.append(
                        f"{where}: binding {label} is not a {kind.lower()} of {entity}"
                    )
            for role, refs in single.get("projections", {}).items():
                for ref in refs:
                    if ref.get("queryRef") not in names_seen:
                        res.problems.append(
                            f"{where}: projection {role} references {ref.get('queryRef')!r} "
                            "which the query does not select"
                        )


def validate_project(pbip: str | Path, data_dir: str | Path | None = None) -> Result:
    """Validate the PBIP project at `pbip` (the .pbip file or its folder)."""
    res = Result()
    pbip = Path(pbip)
    if pbip.is_dir():
        found = sorted(pbip.glob("*.pbip"))
        if not found:
            res.problems.append(f"{pbip}: no .pbip file")
            return res
        pbip = found[0]
    if not pbip.is_file():
        res.problems.append(f"{pbip}: file not found")
        return res
    try:
        project = json.loads(pbip.read_text())
        report_rel = project["artifacts"][0]["report"]["path"]
    except (KeyError, IndexError, json.JSONDecodeError):
        res.problems.append(f"{pbip.name}: no report artifact")
        return res
    report_dir = pbip.parent / report_rel
    try:
        pbir = json.loads((report_dir / "definition.pbir").read_text())
        model_dir = (report_dir / pbir["datasetReference"]["byPath"]["path"]).resolve()
    except (OSError, KeyError, json.JSONDecodeError):
        res.problems.append(
            f"{report_rel}/definition.pbir: missing or has no byPath dataset reference"
        )
        return res
    if not (model_dir / "definition.pbism").is_file():
        res.problems.append(f"{model_dir.name}/definition.pbism: file is missing")
    definition = model_dir / "definition"
    if not definition.is_dir():
        res.problems.append(f"{model_dir.name}/definition: folder is missing")
        return res

    nodes = _load_model(definition, res)
    _build_tables(nodes, res)
    _check_model_files(nodes, res)
    _check_dax(res)
    _check_dax_semantics(res)
    _check_data(res, Path(data_dir) if data_dir else None)
    _check_registry(res)
    _check_report(report_dir, res)
    return res
