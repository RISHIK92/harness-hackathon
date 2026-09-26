"""FR-25: scan the DIFF, never the file.

Pre-existing debt in a touched file is not ours, and cleaning it would
violate the minimal-diff goal.
"""
from __future__ import annotations

import ast
import re

TODO = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
DEBUG = re.compile(
    r"(\bconsole\.(log|debug)\s*\(|\bdebugger\b|\bpdb\.set_trace\b|"
    r"\bbreakpoint\s*\(|^\s*print\s*\(|\bfmt\.Print|\bdbg!\s*\(|"
    r"\bSystem\.out\.print)", re.M)
COMMENTED_CODE = re.compile(
    r"^\s*(#|//)\s*[\w\[\]\.\"']+\s*[=(].*[)\]:]?\s*$", re.M)
MARKERS = re.compile(r"^\s*(<{5,9}|>{5,9}|={5,9})", re.M)
TRAILING_WS = re.compile(r"[ \t]+$", re.M)


def added_lines(before: str, after: str) -> str:
    import difflib
    out = []
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(),
                                     lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return "\n".join(out)


def scan(before: str, after: str, path: str = "",
         style=None) -> list[str]:
    """Flags for lines WE added. Never for what was already there."""
    added = added_lines(before, after)
    flags: list[str] = []
    if not added.strip():
        return flags

    if TODO.search(added):
        flags.append("todo_marker")
    if DEBUG.search(added):
        flags.append("debug_output")
    if COMMENTED_CODE.search(added):
        flags.append("commented_code")
    if MARKERS.search(added):
        flags.append("edit_markers")
    if TRAILING_WS.search(added):
        flags.append("trailing_whitespace")
    if path.endswith(".py") and _unused_imports_added(before, after):
        flags.append("unused_imports")
    if style and not _indent_matches(added, style):
        flags.append("indent_mismatch")
    return flags


def _indent_matches(added: str, style) -> bool:
    uses_tabs = any(ln.startswith("\t") for ln in added.splitlines())
    if style.indent_char == "\t":
        return uses_tabs or not any(ln.startswith(" ")
                                    for ln in added.splitlines())
    return not uses_tabs


def _imports(source: str) -> set:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names)
    return names


def _unused_imports_added(before: str, after: str) -> bool:
    new = _imports(after) - _imports(before)
    if not new:
        return False
    try:
        tree = ast.parse(after)
    except SyntaxError:
        return False
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            used.add(node.value.id)
    return bool(new - used)
