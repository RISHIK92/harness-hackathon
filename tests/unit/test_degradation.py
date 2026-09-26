"""T10.2 fault injection: every row of SPEC.md 15/36 must degrade, not abort.

Nothing here may end a run except a dead provider and a fatal config error,
and both must exit with a clean tree and a stated reason.
"""
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
from harness import exits
from harness.logging_ui import Logger
from harness.orchestrator import Orchestrator

FIX = ROOT / "tests" / "fixtures"
pytestmark = pytest.mark.skipif(not (FIX / "py-offbyone").is_dir(),
                                reason="fixtures not generated")


def _clean(repo):
    for args in (["reset", "-q"], ["checkout", "--", "."],
                 ["clean", "-qfd", "-e", ".fixture.json"]):
        subprocess.run(["git", *args], cwd=repo, capture_output=True)


def run_with(monkeypatch, name="py-offbyone", after_install=None, **env):
    mock_model.install(monkeypatch)
    if after_install:
        after_install(monkeypatch)
    repo = FIX / name
    _clean(repo)
    meta = json.loads((repo / ".fixture.json").read_text())
    for k, v in {"AI_API_KEY": "sk-ant-api03-FAKEDEGRADE1234567890",
                 "REPO_PATH": str(repo), "HARNESS_NO_CACHE": "1",
                 "ISSUE": meta["issue"], **env}.items():
        monkeypatch.setenv(k, str(v))
    for k in ("HARNESS_DRY_RUN", "HARNESS_TASK_TYPE", "HARNESS_ROUTE"):
        if k not in env:
            monkeypatch.delenv(k, raising=False)
    buf = io.StringIO()
    cfg = C.load(["harness"])
    cfg.work_dir = Path(tempfile.mkdtemp()) / ".harness"
    try:
        rc = Orchestrator(cfg, Logger(stream=buf)).run()
        return rc, buf.getvalue(), cfg
    finally:
        _clean(repo)


# -- search backends --------------------------------------------------------
def test_no_ripgrep_degrades_to_git_grep(monkeypatch):
    import shutil as sh
    real = sh.which
    monkeypatch.setattr(sh, "which",
                        lambda n, *a, **k: None if n == "rg" else real(n))
    rc, out, cfg = run_with(monkeypatch)
    assert "slow_search" in out
    assert rc == exits.SUCCESS


def test_no_git_falls_back_to_the_python_walker(monkeypatch, tmp_path):
    from harness.repo.search import Search
    (tmp_path / "a.py").write_text("def target():\n    return 1\n")
    s = Search(tmp_path)
    assert s.backend == "python"
    assert s.grep(r"\btarget\b")


# -- coverage / SBFL --------------------------------------------------------
def test_no_coverage_degrades_to_text_signals(monkeypatch):
    rc, out, cfg = run_with(monkeypatch, HARNESS_NO_COVERAGE="1")
    assert rc in (exits.SUCCESS, exits.PARTIAL)
    baseline = json.loads((cfg.work_dir / "run" / "baseline.json").read_text())
    assert baseline["coverage_ok"] is False
    assert baseline["tests"], "the suite must still run"


def test_green_baseline_yields_no_sbfl_but_still_runs(monkeypatch):
    rc, out, cfg = run_with(monkeypatch, "py-vague")
    assert "route FULL" in out
    assert rc in (exits.PARTIAL, exits.NO_FIX)


# -- linters ----------------------------------------------------------------
def test_no_linter_configured_is_a_no_op(monkeypatch):
    rc, out, cfg = run_with(monkeypatch, HARNESS_LINT_CMD="")
    assert rc == exits.SUCCESS


def test_a_missing_linter_does_not_block(monkeypatch):
    rc, out, cfg = run_with(
        monkeypatch, HARNESS_LINT_CMD="python3 -m not_a_real_linter")
    assert rc == exits.SUCCESS, out[-800:]


def test_forty_lint_errors_do_not_deadlock(monkeypatch):
    rc, out, cfg = run_with(monkeypatch, "py-lint-debt")
    assert rc == exits.SUCCESS
    assert "applied:" in out


# -- tests ------------------------------------------------------------------
def test_no_test_command_degrades_to_unverified(monkeypatch, tmp_path):
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=tmp_path,
                   capture_output=True)
    mock_model.install(monkeypatch)
    monkeypatch.setenv("AI_API_KEY", "sk-ant-api03-FAKEDEGRADE1234567890")
    monkeypatch.setenv("REPO_PATH", str(tmp_path))
    monkeypatch.setenv("ISSUE", "f() returns the wrong value")
    monkeypatch.setenv("HARNESS_NO_CACHE", "1")
    monkeypatch.setenv("HARNESS_TEST_CMD", "true")
    for k in ("HARNESS_DRY_RUN", "HARNESS_TASK_TYPE", "HARNESS_ROUTE"):
        monkeypatch.delenv(k, raising=False)
    cfg = C.load(["harness"])
    cfg.work_dir = Path(tempfile.mkdtemp()) / ".harness"
    buf = io.StringIO()
    rc = Orchestrator(cfg, Logger(stream=buf)).run()
    assert rc in (exits.PARTIAL, exits.NO_FIX)
    assert "internal error" not in buf.getvalue()


def test_red_baseline_is_documented_not_chased(monkeypatch):
    rc, out, cfg = run_with(monkeypatch, "py-red-baseline")
    assert "red_baseline" in out
    assert rc == exits.SUCCESS


def test_flaky_test_does_not_block(monkeypatch):
    rc, out, cfg = run_with(monkeypatch, "py-flaky")
    assert rc == exits.SUCCESS, out[-800:]


# -- provider ---------------------------------------------------------------
def test_discovery_failure_still_runs_with_an_override(monkeypatch):
    """The model listing is unreachable; overrides must carry the run."""
    from harness.model.adapters import anthropic as anth
    from harness.model.types import ProviderError

    def break_listing(mp):
        real = anth.request

        def no_listing(method, url, headers, body=None, timeout=60.0):
            if method == "GET" or url.endswith("/models"):
                raise ProviderError("HTTP 503", status=503)
            return real(method, url, headers, body, timeout)

        mp.setattr(anth, "request", no_listing)
        mp.setattr("time.sleep", lambda *_: None)

    rc, out, cfg = run_with(monkeypatch, after_install=break_listing,
                            HARNESS_MODEL="mock-opus",
                            HARNESS_CHEAP_MODEL="mock-haiku")
    assert "no_discovery" in out
    assert rc == exits.SUCCESS, out[-600:]


def test_dead_provider_exits_cleanly(monkeypatch):
    from harness.model.adapters import anthropic as anth
    from harness.model.types import ProviderError
    repo = FIX / "py-offbyone"
    _clean(repo)

    def dead(*a, **k):
        raise ProviderError("HTTP 500", status=500)

    monkeypatch.setattr(anth, "request", dead)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    monkeypatch.setenv("AI_API_KEY", "sk-ant-api03-FAKEDEGRADE1234567890")
    monkeypatch.setenv("REPO_PATH", str(repo))
    monkeypatch.setenv("ISSUE", "something is broken")
    monkeypatch.setenv("HARNESS_MODEL", "m")
    monkeypatch.setenv("HARNESS_NO_CACHE", "1")
    for k in ("HARNESS_DRY_RUN", "HARNESS_TASK_TYPE", "HARNESS_ROUTE"):
        monkeypatch.delenv(k, raising=False)
    from harness.__main__ import main
    rc = main(["harness"])
    try:
        assert rc in (exits.CONFIG_ERROR, exits.INTERNAL, exits.NO_FIX)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                               capture_output=True, text=True).stdout
        assert not [ln for ln in dirty.splitlines()
                    if ".harness" not in ln], dirty
    finally:
        _clean(repo)


# -- tier / context ---------------------------------------------------------
def test_tiny_context_window_never_overflows(monkeypatch):
    rc, out, cfg = run_with(monkeypatch, HARNESS_TIER="T0",
                            HARNESS_CTX_CAP="8000")
    assert rc in (exits.SUCCESS, exits.PARTIAL)
    assert "tier profile    T0" in out


def test_single_model_degrades(monkeypatch):
    rc, out, cfg = run_with(monkeypatch, HARNESS_MODEL="only-model",
                            HARNESS_CHEAP_MODEL="only-model")
    assert "single_model" in out


def test_budget_exhaustion_still_reports(monkeypatch):
    rc, out, cfg = run_with(monkeypatch, HARNESS_TOKEN_BUDGET="1200")
    assert "RESULT" in out
    assert rc != exits.INTERNAL
