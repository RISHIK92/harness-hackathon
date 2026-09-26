"""Windowed reads, neighbour functions and the computed style profile.

FR-22 and FR-23 are satisfied by CODE, not by prompt wording: the three
adjacent functions and the measured style of the file are injected whether or
not the model thought to ask for them.
"""
from __future__ import annotations

import ast
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Symbol:
    name: str
    kind: str            # function | class | method
    start: int           # 1-indexed, inclusive
    end: int
    parent: str = ""

    @property
    def qualname(self) -> str:
        return f"{self.parent}.{self.name}" if self.parent else self.name


@dataclass
class StyleProfile:
    indent_char: str = " "
    indent_width: int = 4
    quote: str = '"'
    max_line: int = 88
    func_case: str = "snake_case"
    var_case: str = "snake_case"
    const_case: str = "UPPER_SNAKE"
    docstrings: bool = True
    docstring_style: str = '"""'
    error_idiom: str = "raise"
    annotations: bool = False
    comment_density: float = 0.0
    import_style: str = "absolute"
    notes: list = field(default_factory=list)

    def render(self) -> str:
        ind = ("tab" if self.indent_char == "\t"
               else f"{self.indent_width} spaces")
        return (
            f"  indent {ind}          quotes {self.quote}        "
            f"max line {self.max_line}\n"
            f"  functions {self.func_case}    variables {self.var_case}    "
            f"constants {self.const_case}\n"
            f"  errors handled by {self.error_idiom}\n"
            f"  type annotations: {'yes' if self.annotations else 'no'}\n"
            f"  docstrings: {'yes, ' + self.docstring_style if self.docstrings else 'no'}"
        )


# ---------------------------------------------------------------- symbols
def symbols(path: Path) -> list[Symbol]:
    path = Path(path)
    try:
        text = path.read_text("utf-8", errors="replace")
    except OSError:
        return []
    if path.suffix == ".py":
        out = _py_symbols(text)
        if out:
            return out
    return _regex_symbols(text, path.suffix)


def _py_symbols(text: str) -> list[Symbol]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out: list[Symbol] = []

    def walk(node, parent=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append(Symbol(child.name,
                                  "method" if parent else "function",
                                  child.lineno,
                                  getattr(child, "end_lineno", child.lineno),
                                  parent))
                walk(child, child.name)
            elif isinstance(child, ast.ClassDef):
                out.append(Symbol(child.name, "class", child.lineno,
                                  getattr(child, "end_lineno", child.lineno),
                                  parent))
                walk(child, child.name)

    walk(tree)
    return sorted(out, key=lambda s: s.start)


DEF_PATTERNS = {
    ".js": r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)",
    ".ts": r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)",
    ".go": r"^func\s+(?:\([^)]*\)\s*)?(\w+)",
    ".rs": r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)",
    ".java": r"^\s*(?:public|private|protected).*?\s(\w+)\s*\(",
    ".rb": r"^\s*def\s+(\w+)",
    ".php": r"^\s*(?:public|private|protected)?\s*function\s+(\w+)",
}


def _regex_symbols(text: str, suffix: str) -> list[Symbol]:
    pattern = DEF_PATTERNS.get(suffix)
    if not pattern:
        return []
    rx = re.compile(pattern)
    lines = text.splitlines()
    starts = [(i + 1, m.group(1)) for i, line in enumerate(lines)
              if (m := rx.match(line))]
    out = []
    for idx, (start, name) in enumerate(starts):
        end = starts[idx + 1][0] - 1 if idx + 1 < len(starts) else len(lines)
        out.append(Symbol(name, "function", start, end))
    return out


# ---------------------------------------------------------------- reads
def read_window(path: Path, line: int, before: int = 20,
                after: int = 40) -> str:
    try:
        lines = Path(path).read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    lo = max(0, line - 1 - before)
    hi = min(len(lines), line + after)
    width = len(str(hi))
    return "\n".join(f"{i+1:>{width}} | {lines[i]}" for i in range(lo, hi))


def read_symbol(path: Path, name: str) -> tuple[str, Symbol | None]:
    for sym in symbols(path):
        if sym.name == name or sym.qualname == name:
            return _slice(path, sym.start, sym.end), sym
    return "", None


def _slice(path: Path, start: int, end: int, numbered: bool = False) -> str:
    try:
        lines = Path(path).read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    chunk = lines[max(0, start - 1):end]
    if not numbered:
        return "\n".join(chunk)
    width = len(str(end))
    return "\n".join(f"{start+i:>{width}} | {ln}" for i, ln in enumerate(chunk))


def neighbours(path: Path, name: str, k: int = 3) -> str:
    """FR-22: the k functions adjacent to the target, full bodies."""
    syms = [s for s in symbols(path) if s.kind in ("function", "method")]
    if not syms:
        return ""
    idx = next((i for i, s in enumerate(syms)
                if s.name == name or s.qualname == name), None)
    if idx is None:
        chosen = syms[:k]
    else:
        before = syms[max(0, idx - (k // 2 + 1)):idx]
        after = syms[idx + 1:idx + 1 + k]
        chosen = (before + after)[:k]
        if len(chosen) < k:
            chosen = [s for s in syms if s is not syms[idx]][:k]
    return "\n\n".join(_slice(path, s.start, s.end) for s in chosen)


# ---------------------------------------------------------------- style
CAMEL = re.compile(r"^[a-z]+(?:[A-Z][a-z0-9]*)+$")
PASCAL = re.compile(r"^[A-Z][a-zA-Z0-9]*$")
SNAKE = re.compile(r"^[a-z_][a-z0-9_]*$")
UPPER = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _case_of(names: list[str], default: str) -> str:
    if not names:
        return default
    votes = {"camelCase": 0, "PascalCase": 0, "snake_case": 0,
             "UPPER_SNAKE": 0}
    for n in names:
        if UPPER.match(n) and "_" in n or (UPPER.match(n) and len(n) > 1):
            votes["UPPER_SNAKE"] += 1
        elif SNAKE.match(n):
            votes["snake_case"] += 1
        elif CAMEL.match(n):
            votes["camelCase"] += 1
        elif PASCAL.match(n):
            votes["PascalCase"] += 1
    best = max(votes, key=votes.get)
    return best if votes[best] else default


def style_profile(path: Path) -> StyleProfile:
    """Measured, not inferred. Costs no reasoning tokens."""
    p = StyleProfile()
    try:
        text = Path(path).read_text("utf-8", errors="replace")
    except OSError:
        return p
    lines = text.splitlines()
    if not lines:
        return p

    indents = [len(ln) - len(ln.lstrip(" ")) for ln in lines
               if ln.startswith(" ") and ln.strip()]
    tabs = sum(1 for ln in lines if ln.startswith("\t"))
    if tabs > len(indents):
        p.indent_char, p.indent_width = "\t", 1
    elif indents:
        steps = [i for i in indents if i]
        p.indent_width = min(steps) if steps else 4

    single = len(re.findall(r"'[^'\n]*'", text))
    double = len(re.findall(r'"[^"\n]*"', text))
    p.quote = "'" if single > double * 1.2 else '"'

    lengths = [len(ln) for ln in lines if ln.strip()]
    if lengths:
        lengths.sort()
        idx = min(len(lengths) - 1, int(len(lengths) * 0.95))
        p.max_line = max(79, min(120, lengths[idx]))

    comments = sum(1 for ln in lines
                   if ln.strip().startswith(("#", "//")))
    p.comment_density = round(comments / max(1, len(lines)), 3)

    if Path(path).suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return p
        funcs, consts, variables, annotated, docced, total = [], [], [], 0, 0, 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.append(node.name)
                total += 1
                if ast.get_docstring(node):
                    docced += 1
                if node.returns or any(a.annotation for a in node.args.args):
                    annotated += 1
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        (consts if t.id.isupper() else variables).append(t.id)
        p.func_case = _case_of(funcs, "snake_case")
        p.var_case = _case_of(variables, "snake_case")
        p.const_case = _case_of(consts, "UPPER_SNAKE")
        p.docstrings = bool(total) and docced / total >= 0.5
        p.annotations = bool(total) and annotated / total >= 0.5
        p.error_idiom = ("raise" if re.search(r"\braise\b", text)
                         else "return None" if "return None" in text
                         else "raise")
        if "from ." in text:
            p.import_style = "relative"
    return p


def imports_block(path: Path) -> str:
    try:
        lines = Path(path).read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    out = []
    for ln in lines[:80]:
        s = ln.strip()
        if s.startswith(("import ", "from ")) or (out and not s):
            out.append(ln)
        elif out and s and not s.startswith(("import ", "from ", "#")):
            break
    return "\n".join(out).strip()
