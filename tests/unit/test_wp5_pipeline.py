"""WP5 end-to-end: does the harness produce a patch that actually works?"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import mock_model
from harness import config as C
from harness.logging_ui import Logger
from harness.orchestrator import Orchestrator

FIX = ROOT / "tests" / "fixtures"
PYEXE = str((ROOT / ".venv" / "bin" / "python").resolve())
pytestmark = pytest.mark.skipif(not (FIX / "py-offbyone").is_dir(),
                                reason="fixtures not generated")


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, capture_output=True)


def run_and_test(name, monkeypatch):
    """Run the full pipeline, then run the fixture's own suite on the result."""
    mock_model.install(monkeypatch)
    repo = FIX / name
    _git(repo, "checkout", "--", ".")
    meta = json.loads((repo / ".fixture.json").read_text())
    for k, v in {"AI_API_KEY": "sk-ant-api03-MOCK1234567890",
                 "REPO_PATH": str(repo), "HARNESS_NO_CACHE": "1",
                 "ISSUE": meta["issue"]}.items():
        monkeypatch.setenv(k, v)
    for k in ("HARNESS_DRY_RUN", "HARNESS_TASK_TYPE", "HARNESS_ROUTE"):
        monkeypatch.delenv(k, raising=False)

    buf = io.StringIO()
    cfg = C.load(["harness"])
    cfg.work_dir = Path(tempfile.mkdtemp()) / ".harness"
    try:
        Orchestrator(cfg, Logger(stream=buf)).run()
        result = subprocess.run([PYEXE, "-m", "pytest", "-q"], cwd=repo,
                                capture_output=True, text=True)
        diff = subprocess.run(["git", "diff"], cwd=repo, capture_output=True,
                              text=True).stdout
        changed = subprocess.run(["git", "diff", "--name-only"], cwd=repo,
                                 capture_output=True, text=True).stdout.split()
        return buf.getvalue(), result.stdout, diff, changed, meta
    finally:
        _git(repo, "checkout", "--", ".")


@pytest.mark.parametrize("name", [
    "py-offbyone", "py-none-guard", "py-upstream", "py-lint-debt",
    "py-regression",
])
def test_fix_makes_the_suite_green(name, monkeypatch):
    out, tests, diff, changed, meta = run_and_test(name, monkeypatch)
    assert "applied:" in out, out[-1500:]
    assert "failed" not in tests, tests
    assert "passed" in tests


def test_pre_existing_failures_remain_and_are_untouched(monkeypatch):
    """FR-30: document them, never fix them."""
    out, tests, diff, changed, meta = run_and_test("py-red-baseline",
                                                   monkeypatch)
    assert "2 failed" in tests, tests
    assert changed == ["src/cart/totals.py"]
    assert "tax.py" not in diff


def test_flaky_test_is_not_chased(monkeypatch):
    out, tests, diff, changed, meta = run_and_test("py-flaky", monkeypatch)
    assert changed == ["src/sched/queue.py"]
    assert "test_timing" not in diff


def test_diff_is_proportional_and_scoped(monkeypatch):
    """FR-26 and C4/C5 together."""
    for name in ("py-offbyone", "py-none-guard", "py-upstream"):
        out, tests, diff, changed, meta = run_and_test(name, monkeypatch)
        added = sum(1 for ln in diff.splitlines()
                    if ln.startswith("+") and not ln.startswith("+++"))
        assert added <= max(6, meta["reference_diff_lines"] * 2), (name, added)
        assert changed == [meta["target_file"]], (name, changed)


def test_no_slop_in_generated_diffs(monkeypatch):
    """FR-24/FR-25: no explanatory comments, no defensive scaffolding."""
    for name in ("py-offbyone", "py-none-guard", "py-upstream", "py-flaky"):
        out, tests, diff, changed, meta = run_and_test(name, monkeypatch)
        added = "\n".join(ln[1:] for ln in diff.splitlines()
                          if ln.startswith("+") and not ln.startswith("+++"))
        assert "TODO" not in added and "FIXME" not in added
        assert "print(" not in added
        assert "# fix" not in added.lower()
        assert "import " not in added, f"{name}: no new imports expected"


def test_unappliable_edits_retry_then_revert(monkeypatch):
    """Three rejected attempts must leave the tree exactly as it was."""
    repo = FIX / "py-offbyone"
    _git(repo, "checkout", "--", ".")
    original = (repo / "src" / "dateparse" / "parser.py").read_text()

    mock_model.install(monkeypatch)
    real_respond = mock_model.respond

    def broken(body, wire="openai"):
        prompt = "\n".join(m.get("content", "") for m in body.get("messages", []))
        if "OUTPUT FORMAT" in prompt:
            return mock_model._reply(prompt, "I am not going to do that.",
                                     wire=wire)
        return real_respond(body, wire)

    monkeypatch.setattr(mock_model, "respond", broken)
    meta = json.loads((repo / ".fixture.json").read_text())
    for k, v in {"AI_API_KEY": "sk-ant-api03-MOCK1234567890",
                 "REPO_PATH": str(repo), "HARNESS_NO_CACHE": "1",
                 "ISSUE": meta["issue"]}.items():
        monkeypatch.setenv(k, v)
    for k in ("HARNESS_DRY_RUN", "HARNESS_TASK_TYPE", "HARNESS_ROUTE"):
        monkeypatch.delenv(k, raising=False)

    buf = io.StringIO()
    cfg = C.load(["harness"])
    cfg.work_dir = Path(tempfile.mkdtemp()) / ".harness"
    try:
        rc = Orchestrator(cfg, Logger(stream=buf)).run()
        assert "edit rejected" in buf.getvalue()
        assert "no appliable edit after 3 attempts" in buf.getvalue()
        assert (repo / "src" / "dateparse" / "parser.py").read_text() == original
    finally:
        _git(repo, "checkout", "--", ".")


# -- WP6 gates, end to end --------------------------------------------------
def test_verification_gate_order_and_judge(monkeypatch):
    out, tests, diff, changed, meta = run_and_test("py-offbyone", monkeypatch)
    order = [ln for ln in out.splitlines() if "[P4 VERIFY" in ln]
    joined = "\n".join(order)
    assert "lint: clean" in joined
    assert "oracle" in joined and "PASS" in joined
    assert "scoped tests" in joined
    assert "full suite" in joined
    assert "diff sanity: YES" in joined
    assert "best practices" in joined
    # lint must precede tests, tests must precede the judge
    assert joined.index("lint") < joined.index("scoped tests")
    assert joined.index("scoped tests") < joined.index("diff sanity")


def test_full_suite_runs_once_not_per_cycle(monkeypatch):
    out, tests, diff, changed, meta = run_and_test("py-none-guard", monkeypatch)
    assert out.count("full suite (pre-submission gate)") == 1


def test_fixed_transition_is_reported(monkeypatch):
    """A previously-failing test now passing is hard evidence for C2."""
    out, tests, diff, changed, meta = run_and_test("py-offbyone", monkeypatch)
    assert "now passing" in out


def test_lint_debt_fixture_does_not_block_the_run(monkeypatch):
    """The whole point of scoping the lint gate."""
    out, tests, diff, changed, meta = run_and_test("py-lint-debt", monkeypatch)
    assert "lint: clean" in out or "no new diagnostics" in out
    assert "applied:" in out
    assert "failed" not in tests
