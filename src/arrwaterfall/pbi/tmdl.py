"""A small TMDL parser: indentation-based objects, `key: value` properties and expressions.

It covers the subset the project uses (model, table, column, measure, partition, relationship,
expression, ref) and reports malformed files with file and line number.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

KINDS = {
    "model",
    "table",
    "column",
    "measure",
    "partition",
    "relationship",
    "expression",
    "ref",
    "hierarchy",
    "level",
    "annotation",
    "culture",
    "role",
    "database",
}

_PROPERTY = re.compile(r"^([A-Za-z][\w.]*)\s*:\s*(.*)$")
_EXPR_PROPERTY = re.compile(r"^([A-Za-z]\w*)\s*=\s*(.*)$")
_HEADER = re.compile(r"^([A-Za-z]+)(?:\s+(.*))?$")


class TmdlError(ValueError):
    def __init__(self, file: str, line: int, message: str):
        super().__init__(f"{file}:{line}: {message}")
        self.file, self.line, self.message = file, line, message


@dataclass
class Node:
    kind: str
    name: str
    line: int
    file: str
    expression: str | None = None
    props: dict[str, str] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)

    def child(self, kind: str, name: str) -> Node | None:
        return next((c for c in self.children if c.kind == kind and c.name == name), None)

    def of_kind(self, kind: str) -> list[Node]:
        return [c for c in self.children if c.kind == kind]


def _split_name(rest: str, file: str, line: int) -> tuple[str, str | None]:
    """Split `name [= expression]` where name may be single-quoted."""
    rest = rest.strip()
    if rest.startswith("'"):
        i, name = 1, []
        while i < len(rest):
            if rest[i] == "'":
                if rest[i + 1 : i + 2] == "'":
                    name.append("'")
                    i += 2
                    continue
                break
            name.append(rest[i])
            i += 1
        else:
            raise TmdlError(file, line, "unterminated quoted name")
        tail = rest[i + 1 :].strip()
        name_s = "".join(name)
    else:
        m = re.match(r"^([^\s=]+)\s*(.*)$", rest)
        if not m:
            return "", None
        name_s, tail = m.group(1), m.group(2).strip()
    if tail.startswith("="):
        return name_s, tail[1:].strip()
    if tail:
        # e.g. `partition X = m` is handled above; anything else after a name is a defect
        raise TmdlError(file, line, f"unexpected text after name {name_s!r}: {tail!r}")
    return name_s, None


def parse_tmdl(text: str, file: str = "<tmdl>") -> list[Node]:
    """Parse TMDL text into a forest of nodes."""
    roots: list[Node] = []
    stack: list[Node] = []  # ancestors of the current line; depth == len(stack)
    # open expression: (collector, min_indent, buffer); collector(text) stores the finished text
    open_expr: tuple[Callable[[str], None], int, list[str]] | None = None

    def close_expr() -> None:
        nonlocal open_expr
        if open_expr is not None:
            collector, _, buffer = open_expr
            collector("\n".join(buffer).strip())
            open_expr = None

    for no, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.strip().startswith("//"):
            continue  # blank lines, comments and /// descriptions
        stripped = raw.lstrip("\t")
        indent = len(raw) - len(stripped)

        if open_expr is not None and indent >= open_expr[1]:
            open_expr[2].append(stripped)
            continue
        close_expr()
        if stripped[:1] == " ":
            raise TmdlError(file, no, "indentation must use tabs only")

        del stack[indent:]
        if indent != len(stack):
            raise TmdlError(
                file, no, f"unexpected indentation (level {indent}, expected {len(stack)})"
            )
        parent = stack[-1] if stack else None

        m = _PROPERTY.match(stripped)
        if m:
            if parent is None:
                raise TmdlError(file, no, "property outside any object")
            parent.props[m.group(1)] = m.group(2).strip()
            continue
        m = _EXPR_PROPERTY.match(stripped)
        if m:
            if parent is None:
                raise TmdlError(file, no, "property outside any object")
            key, first = m.group(1), m.group(2).strip()
            holder = parent

            def store_prop(value: str, holder: Node = holder, key: str = key) -> None:
                holder.props[key] = value

            open_expr = (store_prop, indent + 1, [first] if first else [])
            continue
        if re.fullmatch(r"[A-Za-z]\w*", stripped) and stripped not in KINDS:
            if parent is None:
                raise TmdlError(file, no, "flag outside any object")
            parent.props[stripped] = "true"  # bare flag such as discourageImplicitMeasures
            continue
        h = _HEADER.match(stripped)
        if not h or h.group(1) not in KINDS:
            word = stripped.split()[0]
            raise TmdlError(file, no, f"unrecognised line starting with {word!r}")
        kind = h.group(1)
        if kind == "ref":
            sub = _HEADER.match(h.group(2) or "")
            if not sub or not sub.group(2):
                raise TmdlError(file, no, "malformed ref")
            name, _ = _split_name(sub.group(2), file, no)
            node = Node("ref", f"{sub.group(1)} {name}", no, file)
        else:
            name, expr = _split_name(h.group(2) or "", file, no)
            if not name and kind != "database":
                raise TmdlError(file, no, f"{kind} without a name")
            node = Node(kind, name, no, file, expression=expr)
            if kind in {"measure", "expression"} and expr is not None:

                def store_expr(value: str, node: Node = node) -> None:
                    node.expression = value

                open_expr = (store_expr, indent + 2, [expr] if expr else [])
        (parent.children if parent else roots).append(node)
        stack.append(node)
    close_expr()
    return roots


def parse_file(path: Path) -> list[Node]:
    return parse_tmdl(path.read_text(), file=path.name)
