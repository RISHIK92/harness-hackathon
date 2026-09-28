"""FR-28: choose the tests that cover the changed files (SPEC.md 9.3).

A five-step ladder, because naming heuristics alone silently select nothing
on a repository that does not follow them.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Scope:
    selector: str = ""
    tests: list = None
    method: str = ""

    def __bool__(self) -> bool:
        return bool(self.selector)


TEST_DIR = re.compile(r"(^|/)(tests?|spec|__tests__)(/|$)")
IDENT = re.compile(r"[A-Za-z_][\w.]*")


def _join(paths: list) -> str:
    """Test files as shell arguments: a file name is not a command."""
    return " ".join(shlex.quote(p) for p in paths)


def _stem(path: str) -> str:
    """The importable name. For a package init that is the directory."""
    p = Path(path)
    if p.stem == "__init__":
        return p.parent.name
    return p.stem


def select(repo: Path, changed: list, symbols: list, toolchain,
           search) -> Scope:
    all_files = search.files()
    tests = [f for f in all_files if _is_test(f)]
    if not tests:
        return Scope(method="none: repository has no test files")

    # 1. same-name test files
    stems = {_stem(c) for c in changed}
    named = [t for t in tests
             if _stem(t).replace("test_", "").replace("_test", "")
             .replace(".test", "") in stems]
    if named:
        return Scope(selector=_join(named), tests=named,
                     method="same-name test file")

    # 2. tests importing the changed module (or its package)
    importers = []
    packages = {Path(c).parent.name for c in changed if Path(c).parent.name}
    for stem in stems | packages:
        if len(stem) < 3:
            continue
        hits = search.grep(rf"\b(import|from)\b.*\b{re.escape(stem)}\b",
                           max_hits=40)
        importers += [h.path for h in hits if _is_test(h.path)]
    importers = sorted(set(importers))
    if importers:
        return Scope(selector=_join(importers), tests=importers,
                     method="test imports the changed module")

    # 3. -k / -run / -t on the changed symbol names
    #
    # The symbols are the model's (P2's plan names them), and this selector
    # is appended to a shell command. Only identifiers are used, and the
    # expression is shell-quoted: `-k "$(...)"` was a command.
    names = [s for s in symbols if s and len(s) > 3 and IDENT.fullmatch(s)]
    if names and toolchain.language == "python":
        expr = " or ".join(names[:4])
        return Scope(selector=f"-k {shlex.quote(expr)}",
                     method="pytest -k on symbols")
    if names and toolchain.language == "go":
        return Scope(selector=f"-run {shlex.quote('|'.join(names[:4]))}",
                     method="go test -run on symbols")

    # 4. the nearest test directory
    for c in changed:
        parts = Path(c).parts
        for i in range(len(parts) - 1, 0, -1):
            cand = "/".join(parts[:i] + ("tests",))
            if any(t.startswith(cand) for t in tests):
                sel = [t for t in tests if t.startswith(cand)]
                return Scope(selector=_join(sel), tests=sel,
                             method="nearest test directory")

    # 5. nothing found
    return Scope(method="none: falling back to the full suite")


def _is_test(path: str) -> bool:
    name = Path(path).name
    return bool(TEST_DIR.search(path) or name.startswith("test_")
                or name.endswith(("_test.py", "_test.go", ".test.ts",
                                  ".test.js", "_spec.rb")))
