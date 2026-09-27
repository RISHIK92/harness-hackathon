"""Localize from the failing tests themselves (SPEC.md 21.6).

§21 localizes with coverage, which needs an instrumented run -- available
for Python and nothing else here. On a JavaScript repository every signal
came back empty: SBFL had no coverage, and the lexical signal greps the
issue's identifiers, which are the words a reporter uses ("bucket") rather
than the words in the code (`projectBreakdown`). An empty signal set means
an empty plan, and an empty plan means the run produces nothing at all.

But a failing test is a fact, and it is pointing directly at the code. A
test file imports what it tests. So: find the file that contains the failing
test, read its imports, and the code under test is among them. That is what
a person does, it needs no instrumentation, and it works in any language
with an import statement.

Deliberately narrow: this only ever returns files that exist in the
repository, and it prefers the ones the *failing* tests reach.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..repo.search import is_source
from .sbfl import is_test_path

# `import x from "./a.js"`, `require("../b")`, `from .c import d`.
JS_IMPORT = re.compile(r"""(?:from|require\s*\()\s*['"](?P<path>[^'"]+)['"]""")
PY_IMPORT = re.compile(
    r"^\s*(?:from\s+(?P<from>[.\w]+)\s+import|import\s+(?P<mod>[.\w]+))",
    re.M)

JS_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")
INDEX_NAMES = tuple(f"index{s}" for s in JS_SUFFIXES)

MAX_TESTS = 8
MAX_FILES = 6


def _clean(name: str) -> str:
    """A test id down to something greppable.

    Runners spell ids differently -- `pkg.mod::test_x`, `suite > case`,
    `path/to/file.js > name` -- and the part worth searching for is the
    human-readable tail.
    """
    tail = re.split(r"::|\s>\s|\s-\s", name.strip())[-1].strip()
    return tail.strip("'\"")


def test_files_for(search, failing: list) -> list:
    """The test files that contain these failing tests."""
    found: list = []
    for raw in failing[:MAX_TESTS]:
        name = _clean(raw)
        if len(name) < 6:
            continue
        try:
            hits = search.grep(re.escape(name), max_hits=8)
        except Exception:
            continue
        for hit in hits:
            if hit.path not in found:
                found.append(hit.path)
    return found


def _resolve_js(repo: Path, origin: str, spec: str) -> str | None:
    if not spec.startswith("."):
        return None                      # a package, not this repository
    base = (Path(origin).parent / spec).as_posix()
    base = str(Path(base).resolve()) if False else base
    # Normalise "a/b/../c" without touching the filesystem.
    parts: list = []
    for part in base.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    candidate = "/".join(parts)

    attempts = [candidate]
    if not candidate.endswith(JS_SUFFIXES):
        attempts += [candidate + s for s in JS_SUFFIXES]
        attempts += [f"{candidate}/{i}" for i in INDEX_NAMES]
    for attempt in attempts:
        if (repo / attempt).is_file():
            return attempt
    return None


def _resolve_py(repo: Path, origin: str, module: str) -> str | None:
    if not module:
        return None
    if module.startswith("."):
        base = Path(origin).parent
        for _ in range(len(module) - len(module.lstrip("."))):
            base = base.parent
        tail = module.lstrip(".").replace(".", "/")
        stem = (base / tail).as_posix().lstrip("/")
    else:
        stem = module.replace(".", "/")
    for attempt in (f"{stem}.py", f"{stem}/__init__.py"):
        if (repo / attempt).is_file():
            return attempt
    return None


def imports_of(repo: Path, path: str) -> list:
    """Repository files that `path` imports. Packages are ignored."""
    try:
        text = (repo / path).read_text("utf-8", errors="replace")
    except OSError:
        return []

    out: list = []
    if path.endswith(JS_SUFFIXES):
        for m in JS_IMPORT.finditer(text):
            resolved = _resolve_js(repo, path, m.group("path"))
            if resolved and resolved not in out:
                out.append(resolved)
    elif path.endswith(".py"):
        for m in PY_IMPORT.finditer(text):
            resolved = _resolve_py(repo, path,
                                   m.group("from") or m.group("mod") or "")
            if resolved and resolved not in out:
                out.append(resolved)
    return out


def candidates(repo: Path, search, failing: list) -> list:
    """Source files the failing tests import, best first.

    Empty when the tests import nothing in this repository, which is honest:
    it means this signal has nothing to say, not that there is no bug.
    """
    repo = Path(repo)
    scores: dict = {}
    for test_path in test_files_for(search, failing):
        if not is_test_path(test_path) and not is_source(test_path):
            continue
        for imported in imports_of(repo, test_path):
            if not is_source(imported) or is_test_path(imported):
                continue
            # Reached by more than one failing test is a stronger signal.
            scores[imported] = scores.get(imported, 0) + 1
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [p for p, _ in ranked][:MAX_FILES]
