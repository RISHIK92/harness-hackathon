"""WP7 end-to-end: exit codes, the evidence package, recovery, restore."""
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
PYEXE = str((ROOT / ".venv" / "bin" / "python").resolve())
pytestmark = pytest.mark.skipif(not (FIX / "py-offbyone").is_dir(),
                                reason="fixtures not generated")


def _clean(repo):
    subprocess.run(["git", "reset", "-q"], cwd=repo, capture_output=True)
    subprocess.run(["git", "checkout", "--", "."], cwd=repo,
                   capture_output=True)


def run_full(name, monkeypatch, **env):
    mock_model.install(monkeypatch)
    repo = FIX / name
    _clean(repo)
    meta = json.loads((repo / ".fixture.json").read_text())
    for k, v in {"AI_API_KEY": "sk-ant-api03-MOCK1234567890",
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
        run_dir = cfg.work_dir / "run"
        text_suffixes = {".json", ".md", ".jsonl", ".patch", ".xml", ".rc"}
        artifacts = {p.name: p.read_text("utf-8", errors="replace")
                     for p in run_dir.glob("*")
                     if p.is_file() and p.suffix in text_suffixes}
        return rc, buf.getvalue(), artifacts, repo, meta
    finally:
        _clean(repo)


# -- exit codes (FR-4) ------------------------------------------------------
def test_success_exits_zero(monkeypatch):
    rc, out, art, repo, meta = run_full("py-offbyone", monkeypatch)
    assert rc == exits.SUCCESS
    assert "status            SUCCESS" in out
    assert "(6/6)" in out


def test_missing_key_is_config_error(monkeypatch):
    from harness.__main__ import main
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setenv("ISSUE", "x")
    assert main(["harness"]) == exits.CONFIG_ERROR


def test_no_appliable_edit_exits_no_fix_and_restores(monkeypatch):
    repo = FIX / "py-offbyone"
    _clean(repo)
    target = repo / "src" / "dateparse" / "parser.py"
    original = target.read_text()
    mock_model.install(monkeypatch)
    real = mock_model.respond

    def refuses(body, wire="openai"):
        prompt = "\n".join(m.get("content", "")
                           for m in body.get("messages", []))
        if "OUTPUT FORMAT" in prompt:
            return mock_model._reply(prompt, "No.", wire=wire)
        return real(body, wire)

    monkeypatch.setattr(mock_model, "respond", refuses)
    rc, out, art, _repo, meta = run_full("py-offbyone", monkeypatch)
    assert rc == exits.NO_FIX
    assert "PATCH_NOT_APPLIED" in out
    assert target.read_text() == original


# -- the evidence package (FR-36, SPEC.md 30.1) ----------------------------
def test_run_report_is_written_with_every_section(monkeypatch):
    rc, out, art, repo, meta = run_full("py-red-baseline", monkeypatch)
    assert "run_report.md" in art
    report = art["run_report.md"]
    for heading in ("## 1. Task", "## 2. Investigation", "## 3. Root cause",
                    "## 4. What changed", "## 5. What was NOT changed",
                    "## 6. Verification", "## 7. Confidence", "## 8. Cost"):
        assert heading in report, heading
    assert "was already failing before this run" in report
    assert "tokens:" in report and "per phase" in report


def test_artifacts_are_all_written(monkeypatch):
    rc, out, art, repo, meta = run_full("py-offbyone", monkeypatch)
    for name in ("issue.json", "rootcause.json", "scope.json",
                 "baseline.json", "verification.json", "confidence.json",
                 "diff.patch", "trajectory.jsonl", "run_report.md"):
        assert name in art, name
    assert json.loads(art["confidence.json"])["score"] == 6


def test_stdout_carries_the_diff_and_the_report(monkeypatch):
    """FR-36: the confidence report is printed alongside the diff."""
    rc, out, art, repo, meta = run_full("py-none-guard", monkeypatch)
    assert "=== DIFF ===" in out
    assert "diff --git" in out
    assert out.index("confidence        ") < out.index("=== DIFF ===")


def test_harness_artifacts_never_appear_in_the_diff(monkeypatch):
    """C4: .harness must be excluded, never added to the repo's .gitignore."""
    rc, out, art, repo, meta = run_full("py-offbyone", monkeypatch)
    diff = out.split("=== DIFF ===")[1]
    assert ".harness" not in diff
    assert "gitignore" not in diff
    exclude = (repo / ".git" / "info" / "exclude").read_text()
    assert ".harness/" in exclude


# -- the loop (FR-33) -------------------------------------------------------
def test_recovery_loop_retries_then_succeeds(monkeypatch):
    """First implementation attempt is unappliable; the second works."""
    state = {"n": 0}
    mock_model.install(monkeypatch)
    real = mock_model.respond

    def flaky(body, wire="openai"):
        prompt = "\n".join(m.get("content", "")
                           for m in body.get("messages", []))
        if "OUTPUT FORMAT" in prompt:
            state["n"] += 1
            if state["n"] == 1:
                return mock_model._reply(
                    prompt, "<<<<<<< SEARCH src/dateparse/parser.py\n"
                            "this text is nowhere in the file\n=======\n"
                            "whatever\n>>>>>>> REPLACE", wire=wire)
        return real(body, wire)

    monkeypatch.setattr(mock_model, "respond", flaky)
    rc, out, art, repo, meta = run_full("py-offbyone", monkeypatch)
    assert state["n"] >= 2
    assert "PATCH_NOT_APPLIED" in out
    assert rc == exits.SUCCESS, out[-1200:]


def test_cycle_count_is_reported(monkeypatch):
    rc, out, art, repo, meta = run_full("py-upstream", monkeypatch)
    assert "cycles used       1 of 5" in out


def test_max_cycles_is_honoured(monkeypatch):
    """A model that always produces a scope violation must stop at the cap."""
    mock_model.install(monkeypatch)
    real = mock_model.respond

    def out_of_scope(body, wire="openai"):
        prompt = "\n".join(m.get("content", "")
                           for m in body.get("messages", []))
        if "OUTPUT FORMAT" in prompt:
            return mock_model._reply(
                prompt, "<<<<<<< SEARCH tests/test_parser.py\n"
                        "def test_normalize():\n=======\n"
                        "def test_normalize_renamed():\n>>>>>>> REPLACE",
                wire=wire)
        return real(body, wire)

    monkeypatch.setattr(mock_model, "respond", out_of_scope)
    rc, out, art, repo, meta = run_full("py-offbyone", monkeypatch,
                                        HARNESS_MAX_CYCLES=2)
    assert rc in (exits.NO_FIX, exits.PARTIAL)
    assert "SCOPE_VIOLATION" in out or "scope" in out.lower()
    assert subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                          capture_output=True, text=True).stdout.strip() == ""


# -- cost transparency (SPEC.md 30.3) --------------------------------------
def test_cost_is_reported(monkeypatch):
    rc, out, art, repo, meta = run_full("py-offbyone", monkeypatch)
    assert "tokens" in out
    assert "wall" in out
    report = art["run_report.md"]
    assert "- tokens:" in report
    assert "- wall clock:" in report
