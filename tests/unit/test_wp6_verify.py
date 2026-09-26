"""WP6: the lint gate, scoped selection, gate order, judges."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harness.logging_ui import Logger
from harness.verify import scope_tests
from harness.verify.judges import _mentions_diff_identifier
from harness.verify.lint_gate import (NOT_INSTALLED, capture_baseline, concise,
                                      gate, parse_diagnostics)
from harness.verify.toolchain import Toolchain, discover_toolchain

FIX = ROOT / "tests" / "fixtures"
pytestmark = pytest.mark.skipif(not (FIX / "py-lint-debt").is_dir(),
                                reason="fixtures not generated")


def silent():
    return Logger(stream=io.StringIO())


# -- T6.1 the lint gate: the single most important test in the suite --------
def test_pre_existing_lint_debt_does_not_block():
    """40 pre-existing errors must not deadlock every cycle (SPEC.md 9.2)."""
    repo = FIX / "py-lint-debt"
    tc = discover_toolchain(repo)
    base = capture_baseline(repo, tc, silent())
    assert len(base) >= 40, "the fixture should carry real lint debt"

    res = gate(repo, tc, ["src/legacy/debt.py"], base, silent())
    assert res.ran
    assert not res.blocks
    assert len(res.pre_existing) >= 40


def test_a_new_diagnostic_does_block():
    repo = FIX / "py-lint-debt"
    tc = discover_toolchain(repo)
    base = capture_baseline(repo, tc, silent())
    target = repo / "src" / "legacy" / "calc.py"
    original = target.read_text()
    try:
        target.write_text(original + "\nimport os\n")
        res = gate(repo, tc, ["src/legacy/calc.py"], base, silent())
        assert res.blocks
        assert any("calc.py" in d for d in res.new)
    finally:
        target.write_text(original)


def test_gate_is_scoped_to_changed_files():
    """Touching one clean file must not surface another file's debt."""
    repo = FIX / "py-lint-debt"
    tc = discover_toolchain(repo)
    base = capture_baseline(repo, tc, silent())
    res = gate(repo, tc, ["src/legacy/calc.py"], base, silent())
    assert not res.blocks
    assert not any("debt.py" in d for d in res.new + res.pre_existing)


def test_no_linter_configured_is_a_no_op(tmp_path):
    res = gate(tmp_path, Toolchain(), ["a.py"], set(), silent())
    assert not res.ran and not res.blocks
    assert "no linter" in res.reason


def test_a_missing_linter_degrades_rather_than_passing_vacuously(tmp_path):
    """A gate that silently always succeeds is worse than no gate."""
    tc = Toolchain(language="python",
                   lint_cmd="python3 -m definitely_not_a_linter")
    res = gate(tmp_path, tc, ["a.py"], set(), silent())
    assert not res.ran
    assert "not installed" in res.reason


def test_diagnostic_parser_handles_ruff_full_and_concise_forms():
    concise_out = "src/a.py:13:1: F401 `os` imported but unused\n"
    full_out = ("  --> src/a.py:13:1\n   |\n13 | import os\n   | ^^^^ F401\n")
    assert "src/a.py:13:F401" in parse_diagnostics(concise_out)
    assert any(d.startswith("src/a.py:13") for d in parse_diagnostics(full_out))


def test_ansi_is_stripped_before_parsing():
    coloured = "\x1b[1msrc/a.py\x1b[0m:5:1: E501 line too long\n"
    assert parse_diagnostics(coloured)


def test_concise_flag_is_appended_for_known_linters():
    assert concise("python -m ruff check").endswith("--output-format=concise")
    assert "--no-pretty" in concise("mypy .")
    assert concise("some-unknown-linter") == "some-unknown-linter"
    assert concise("ruff check --output-format=json").count("output-format") == 1


def test_not_installed_detection():
    assert NOT_INSTALLED.search("No module named ruff")
    assert NOT_INSTALLED.search("ruff: command not found")
    assert not NOT_INSTALLED.search("src/a.py:1:1: F401 unused")


# -- T6.2 scoped test selection --------------------------------------------
def test_same_name_test_file_is_found():
    from harness.repo.search import Search
    repo = FIX / "py-offbyone"
    tc = discover_toolchain(repo)
    scope = scope_tests.select(repo, ["src/dateparse/parser.py"], ["parse_date"],
                               tc, Search(repo))
    assert scope
    assert "tests/test_parser.py" in scope.selector
    assert scope.method == "same-name test file"


def test_selection_falls_through_to_importing_tests():
    from harness.repo.search import Search
    repo = FIX / "py-cross-caller"
    tc = discover_toolchain(repo)
    scope = scope_tests.select(repo, ["src/geo/routes.py"], ["leg_length"],
                               tc, Search(repo))
    assert scope, scope.method
    assert "test_distance.py" in scope.selector


def test_selection_on_every_fixture():
    from harness.repo.search import Search
    found = 0
    for d in sorted(FIX.iterdir()):
        if not d.is_dir():
            continue
        tc = discover_toolchain(d)
        src = [str(p.relative_to(d)) for p in sorted(d.rglob("*.py"))
               if "test" not in p.name and "src" in str(p)
               and p.name != "__init__.py"][:1]
        if not src:
            continue
        if scope_tests.select(d, src, [], tc, Search(d)):
            found += 1
    assert found >= 10, f"scoped selection worked on only {found} fixtures"


def test_package_init_selects_the_package_tests():
    """A change to __init__.py is a change to the package, not to a module."""
    from harness.repo.search import Search
    repo = FIX / "py-offbyone"
    tc = discover_toolchain(repo)
    scope = scope_tests.select(repo, ["src/dateparse/__init__.py"], [], tc,
                               Search(repo))
    assert scope, scope.method
    assert "test_parser.py" in scope.selector


def test_repo_without_tests_reports_none(tmp_path):
    class NoFiles:
        def files(self):
            return ["src/a.py"]

        def grep(self, *a, **k):
            return []

    scope = scope_tests.select(tmp_path, ["src/a.py"], [], Toolchain(),
                               NoFiles())
    assert not scope
    assert "no test files" in scope.method


# -- T6.4 judges -----------------------------------------------------------
def test_anti_sycophancy_requires_a_named_identifier():
    diff = ("--- a/src/a.py\n+++ b/src/a.py\n"
            "-    return parts[0]\n+    if len(parts) != 3:\n")
    assert _mentions_diff_identifier("the len check on parts", diff)
    assert not _mentions_diff_identifier("it just works now", diff)
    assert not _mentions_diff_identifier("", diff)


# -- regression: build artifacts are not "changed files" --------------------
def test_pycache_is_never_reported_as_changed():
    """Running the suite leaves .pyc files; linting one blocks spuriously."""
    from harness.repo.workspace import Workspace
    repo = FIX / "py-offbyone"
    ws = Workspace(repo)
    ws.exclude_harness_dir()
    junk = repo / "__pycache__"
    junk.mkdir(exist_ok=True)
    (junk / "mod.cpython-313.pyc").write_bytes(b"\x00\x01")
    (repo / ".coverage").write_text("x")
    try:
        changed = ws.changed_files()
        assert not any("__pycache__" in f or f.endswith(".pyc")
                       or f.endswith(".coverage") for f in changed), changed
    finally:
        import shutil
        shutil.rmtree(junk, ignore_errors=True)
        (repo / ".coverage").unlink(missing_ok=True)


def test_lint_gate_ignores_non_source_paths(tmp_path):
    tc = discover_toolchain(FIX / "py-lint-debt")
    res = gate(FIX / "py-lint-debt", tc, ["__pycache__/x.pyc", "data.json"],
               set(), silent())
    assert not res.ran and not res.blocks
    assert "no lintable files" in res.reason
