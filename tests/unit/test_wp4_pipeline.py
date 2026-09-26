"""WP4 end-to-end: P0 triage -> localization -> P1 -> P2, on real fixtures.

Runs with HARNESS_DRY_RUN so nothing is written: FR-12's structural guarantee
is that no code exists before the root-cause report.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import mock_model
from harness import config as C
from harness.logging_ui import Logger
from harness.orchestrator import Orchestrator
from harness.phases.p0_triage import effective_type, triage
from harness.repo.search import Search

FIX = ROOT / "tests" / "fixtures"
pytestmark = pytest.mark.skipif(not (FIX / "py-offbyone").is_dir(),
                                reason="fixtures not generated")


def run_fixture(name, monkeypatch, tmp_path, **overrides):
    mock_model.install(monkeypatch)
    repo = FIX / name
    monkeypatch.setenv("AI_API_KEY", "sk-ant-api03-MOCK1234567890")
    monkeypatch.setenv("REPO_PATH", str(repo))
    monkeypatch.setenv("HARNESS_DRY_RUN", "1")
    monkeypatch.setenv("HARNESS_NO_CACHE", "1")
    meta = json.loads((repo / ".fixture.json").read_text())
    monkeypatch.setenv("ISSUE", meta["issue"])
    for k, v in overrides.items():
        monkeypatch.setenv(k, str(v))

    buf = io.StringIO()
    cfg = C.load(["harness"])
    cfg.work_dir = tmp_path / ".harness"
    log = Logger(level="info", secrets=[cfg.api_key], stream=buf)
    orch = Orchestrator(cfg, log)
    rc = orch.run()
    artifacts = {p.name: json.loads(p.read_text())
                 for p in (cfg.work_dir / "run").glob("*.json")}
    return rc, buf.getvalue(), artifacts, meta, cfg


# -- FR-12: no code before the root cause ----------------------------------
def test_dry_run_writes_nothing(monkeypatch, tmp_path):
    from harness.repo.workspace import Workspace
    repo = FIX / "py-offbyone"
    before = Workspace(repo).changed_files()
    run_fixture("py-offbyone", monkeypatch, tmp_path)
    assert Workspace(repo).changed_files() == before


def test_investigation_has_no_write_tool():
    """FR-12 is structural: P1 cannot reach an edit function."""
    import harness.phases.p1_investigate as p1
    source = Path(p1.__file__).read_text()
    for forbidden in ("apply_edit", "write_text(", "edit.apply",
                      "from ..edit"):
        assert forbidden not in source, forbidden


# -- the happy path --------------------------------------------------------
def test_offbyone_end_to_end(monkeypatch, tmp_path):
    rc, out, art, meta, cfg = run_fixture("py-offbyone", monkeypatch, tmp_path)
    assert "route ORACLE" in out
    assert "baseline: 3 passed, 1 failed" in out
    assert "coverage localization" in out
    assert art["rootcause.json"]["classification"] == "logic"
    assert art["rootcause.json"]["confidence"] == "high"
    assert art["rootcause.json"]["files"][0]["path"] == meta["target_file"]
    assert art["scope.json"]["files_to_change"][0]["path"] == meta["target_file"]
    assert art["scope.json"]["kind"] == "single_file"
    assert art["issue.json"]["task_type"] == "BUG_FIX"


def test_upstream_traces_callers(monkeypatch, tmp_path):
    rc, out, art, meta, cfg = run_fixture("py-upstream", monkeypatch, tmp_path)
    assert art["rootcause.json"]["files"][0]["path"] == meta["target_file"]
    assert "upstream:" in out or "callers:" in json.dumps(art["rootcause.json"])


# -- FR-29/FR-30: pre-existing failures --------------------------------------
def test_red_baseline_documents_but_does_not_chase(monkeypatch, tmp_path):
    rc, out, art, meta, cfg = run_fixture("py-red-baseline", monkeypatch,
                                          tmp_path)
    assert "red_baseline" in out
    assert "3 tests already failing" in out
    # localization must target the issue's file, not the pre-existing failures
    assert art["rootcause.json"]["files"][0]["path"] == "src/cart/totals.py"
    assert "tax.py" not in art["scope.json"]["files_to_change"][0]["path"]


# -- FR-14: external factors -----------------------------------------------
def test_env_missing_is_classified_config(monkeypatch, tmp_path):
    monkeypatch.delenv("MAILER_ENDPOINT", raising=False)
    rc, out, art, meta, cfg = run_fixture("py-env-missing", monkeypatch,
                                          tmp_path)
    assert art["issue.json"]["task_type"] == "CONFIG"
    assert art["rootcause.json"]["classification"] == "config"
    ef = art["rootcause.json"]["external_factors"]
    assert ef["ruled_out"] is False
    assert any("MAILER_ENDPOINT" in f["detail"] for f in ef["findings"])


def test_dep_bump_finds_the_lockfile_skew(monkeypatch, tmp_path):
    rc, out, art, meta, cfg = run_fixture("py-dep-bump", monkeypatch, tmp_path)
    ef = art["rootcause.json"]["external_factors"]
    assert any("requests" in f["detail"] and "1.9.0" in f["detail"]
               for f in ef["findings"])


# -- FR-15: vague issues ---------------------------------------------------
def test_vague_issue_runs_hypothesis_elimination(monkeypatch, tmp_path):
    rc, out, art, meta, cfg = run_fixture("py-vague", monkeypatch, tmp_path)
    assert art["issue.json"]["vagueness"] >= 0.5
    assert art["issue.json"]["min_hypotheses"] == 3
    assert "route FULL" in out
    hyps = art["rootcause.json"]["hypotheses"]
    assert len(hyps) >= 3
    # every hypothesis carries an EXECUTED check with a verdict
    for h in hyps:
        assert h["check"] is not None
        assert h["support"] in ("confirmed", "refuted", "inconclusive")
        assert h["result"]


# -- FR-20: callers --------------------------------------------------------
def test_cross_caller_enumerates_call_sites(monkeypatch, tmp_path):
    rc, out, art, meta, cfg = run_fixture("py-cross-caller", monkeypatch,
                                          tmp_path)
    plan = art["scope.json"]
    assert plan["callers_requiring_update"], "FR-20 requires the caller list"
    files = {c.split(":")[0] for c in plan["callers_requiring_update"]}
    assert files >= {"src/geo/routes.py", "src/geo/nearest.py",
                     "src/geo/report.py"}
    assert plan["kind"] == "cross_cutting"


# -- plan hardening --------------------------------------------------------
def test_test_and_lock_paths_are_denied(monkeypatch, tmp_path):
    from harness.phases.p2_scope import NEVER_TOUCH
    for path in ("tests/test_x.py", "src/foo_test.py", "poetry.lock",
                 "package-lock.json", "vendor/lib.py", "app/x_pb2.py",
                 "build/out.py"):
        assert NEVER_TOUCH.search(path), path
    for path in ("src/app/service.py", "lib/parser.py", "main.go"):
        assert not NEVER_TOUCH.search(path), path


def test_conservative_mode_clamps_the_plan(monkeypatch, tmp_path):
    rc, out, art, meta, cfg = run_fixture(
        "py-offbyone", monkeypatch, tmp_path, HARNESS_TASK_TYPE="UNKNOWN")
    assert "conservative" in out
    assert art["scope.json"]["estimated_lines_changed"] <= 15
    assert len(art["scope.json"]["files_to_change"]) == 1


# -- routing ---------------------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("py-offbyone", "ORACLE"),
    ("py-none-guard", "ORACLE"),
    ("py-vague", "FULL"),
    ("py-dep-bump", "FULL"),
])
def test_routes(name, expected, monkeypatch, tmp_path):
    rc, out, art, meta, cfg = run_fixture(name, monkeypatch, tmp_path)
    assert f"route {expected}" in out


def test_forced_route_is_honoured(monkeypatch, tmp_path):
    rc, out, art, meta, cfg = run_fixture("py-offbyone", monkeypatch, tmp_path,
                                          HARNESS_ROUTE="FULL")
    assert "route FULL" in out
    assert "forced by HARNESS_ROUTE" in out


# -- regression: only source files can be callers ---------------------------
def test_data_files_are_never_counted_as_callers(monkeypatch, tmp_path):
    """A JSON fixture quoting `grand_total(...)` is not a call site."""
    rc, out, art, meta, cfg = run_fixture("py-red-baseline", monkeypatch,
                                          tmp_path)
    for site in art["scope.json"]["callers_requiring_update"]:
        assert site.split(":")[0].endswith(
            (".py", ".js", ".ts", ".go", ".rs")), site
    assert ".fixture.json" not in out
    assert art["scope.json"]["kind"] == "single_file"


def test_reported_context_window_is_used(monkeypatch, tmp_path):
    rc, out, art, meta, cfg = run_fixture("py-offbyone", monkeypatch, tmp_path)
    assert "ctx 200000" in out
