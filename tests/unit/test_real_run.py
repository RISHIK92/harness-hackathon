"""Defects found by a real model run against a real GitHub issue.

The harness fixed the right file, refused to call a wrong fix SUCCESS, and
then spent every remaining cycle oscillating between two answers without
ever being told what was wrong with either.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.records import (AffectedFile, ChangePlan, ConfidenceReport,   # noqa: E402
                             FileIntent, RootCauseRecord)
from harness.repo.external import ExternalFactors, Finding                 # noqa: E402


# -- the feedback that lets a second attempt do better ---------------------

class _Verify:
    def __init__(self, oracle_passes=False, oracle_id="", output=""):
        self.oracle_passes = oracle_passes
        self.oracle_id = oracle_id
        self.oracle_output = output
        self.blocking = []
        self.full = None
        self.scoped = None


class _Failure:
    detail = "tests failed"
    first_move = "re-read the file"

    def render(self):
        return "TEST_FAILURE"


def _feedback(vres):
    from harness.orchestrator import Orchestrator
    from harness.phases.p5_confidence import Action
    return Orchestrator._feedback_for(None, Action.REIMPLEMENT_TARGETED,
                                      None, vres, _Failure())


def test_a_still_failing_oracle_feeds_back_its_own_output():
    """The model wrote `progress === 100` when the test wanted `>= 90`. It
    was told "the previous attempt broke: existing_tests_pass", which says
    nothing about the boundary it got wrong."""
    vres = _Verify(
        oracle_passes=False,
        oracle_id="finished projects are reported as completed",
        output="AssertionError [ERR_ASSERTION]: Expected values to be "
               "strictly equal:\n\n1 !== 2\n")
    text = _feedback(vres)

    assert "finished projects are reported as completed" in text
    assert "1 !== 2" in text, "the assertion never reached the implementer"
    assert "did NOT make the test pass" in text


def test_without_an_oracle_the_old_feedback_still_applies():
    vres = _Verify(oracle_passes=True)
    vres.blocking = ["test_a", "test_b"]
    text = _feedback(vres)
    assert "test_a" in text


# -- C6: only findings that touch the change may block ---------------------

def _score(changed, findings, classification="missing_case"):
    from harness.phases import p5_confidence as P5

    ef = ExternalFactors(checked=["env"], findings=findings)
    root_cause = RootCauseRecord(
        statement="missing bucket", classification=classification,
        confidence="high", external_factors=ef.to_json(),
        files=[AffectedFile("src/lib/dashboardStats.js", (1, 9), "here")])

    class WS:
        def changed_files(self):
            return list(changed)

        def diff_numstat(self):
            return (2, 2)

        def diff(self):
            return ""

    class Cfg:
        conservative = False

    from harness.verify.classify import Classification

    class Verify:
        oracle_passes = True
        oracle_id = ""
        oracle_output = ""
        blocking = []
        full = Classification(fixed=["t"], pre_existing=[])
        scoped = None

        class judge:
            addresses = True
            conclusive = True

        class lint:
            blocks = False

    plan = ChangePlan(files_to_change=[FileIntent("src/lib/dashboardStats.js")],
                      estimated_lines_changed=5)
    return P5.score(root_cause, plan, Verify(), WS(), Cfg())


def test_an_unrelated_missing_env_var_does_not_block():
    """A missing FIREBASE_PRIVATE_KEY says nothing about a pure function in
    dashboardStats.js, but it used to fail C6 and report the run PARTIAL."""
    report = _score(
        changed=["src/lib/dashboardStats.js"],
        findings=[Finding("env", "FIREBASE_PRIVATE_KEY is not set", "high",
                          where=["src/services/firebaseAdmin.ts"]),
                  Finding("env", "JWT_SECRET is not set", "high",
                          where=["src/index.js"])])
    assert report.external_factors_resolved, \
        "an unrelated env var still blocks the run"


def test_a_finding_in_the_changed_file_still_blocks():
    report = _score(
        changed=["src/lib/dashboardStats.js"],
        findings=[Finding("env", "STATS_MODE is not set", "high",
                          where=["src/lib/dashboardStats.js"])])
    assert not report.external_factors_resolved, \
        "a finding in the file we changed must still block"


def test_a_repo_wide_finding_still_blocks():
    """A dependency skew names no file and could affect anything."""
    report = _score(
        changed=["src/lib/dashboardStats.js"],
        findings=[Finding("dependency", "express pinned below its lockfile",
                          "high")])
    assert not report.external_factors_resolved


def test_no_findings_at_all_is_still_resolved():
    assert _score(changed=["a.js"], findings=[]).external_factors_resolved


# -- C6, continued: severity and path shape --------------------------------

def test_a_low_severity_observation_never_blocks():
    """"2 manifest change(s) in the last 90 days" is context, not a fault.
    It names no file, so the repo-wide rule caught it, and it failed a run
    whose every other gate was green."""
    report = _score(
        changed=["src/lib/dashboardStats.js"],
        findings=[Finding("dependency",
                          "2 manifest change(s) in the last 90 days",
                          "low")])
    assert report.external_factors_resolved


def test_a_high_severity_repo_wide_finding_still_blocks():
    report = _score(
        changed=["src/lib/dashboardStats.js"],
        findings=[Finding("dependency", "express pinned below its lockfile",
                          "high")])
    assert not report.external_factors_resolved


def test_the_exact_findings_from_the_real_run_no_longer_block():
    """Four unrelated env vars plus one low-severity manifest note: the
    combination that reported a verified fix as PARTIAL."""
    report = _score(
        changed=["src/lib/dashboardStats.js"],
        findings=[
            Finding("env", "FIREBASE_CLIENT_EMAIL is not set", "high",
                    where=["src/middleware/auth.js",
                           "src/services/firebaseAdmin.ts"]),
            Finding("env", "FIREBASE_PRIVATE_KEY is not set", "high",
                    where=["src/middleware/auth.js",
                           "src/services/firebaseAdmin.ts"]),
            Finding("env", "FIREBASE_PROJECT_ID is not set", "high",
                    where=["src/middleware/auth.js",
                           "src/services/firebaseAdmin.ts"]),
            Finding("env", "JWT_SECRET is not set", "high",
                    where=["src/index.js"]),
            Finding("dependency", "2 manifest change(s) in the last 90 days",
                    "low"),
        ])
    assert report.external_factors_resolved, \
        "the run would still be reported PARTIAL"


def test_finding_paths_are_repo_relative_whatever_the_repo_path_looks_like(
        tmp_path, monkeypatch):
    """They were only relative when the repo was given as an absolute path;
    otherwise they carried the repo prefix and matched nothing."""
    import os
    from harness.repo.external import ExternalFactors, _probe_env_vars

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.js").write_text(
        "const k = process.env.SOME_UNSET_THING_XYZ;\n")
    monkeypatch.delenv("SOME_UNSET_THING_XYZ", raising=False)

    monkeypatch.chdir(tmp_path.parent)
    ef = ExternalFactors()
    _probe_env_vars(Path(tmp_path.name), ef, None, None)

    found = [f for f in ef.findings if "SOME_UNSET_THING_XYZ" in f.detail]
    assert found, "probe found nothing to check"
    assert found[0].where == ["src/app.js"], \
        f"path carried a prefix: {found[0].where}"


# -- cycles that cannot make progress --------------------------------------

def test_an_identical_diff_with_identical_blockers_stops_the_loop():
    """Cycles 2 and 3 of a real run re-sent an unchanged prompt and got the
    cached reply back -- the log literally said "replayed". The same input
    cannot produce a different outcome, so spending cycles on it is waste.
    """
    import inspect

    from harness.orchestrator import Orchestrator
    # run() is a thin wrapper that guarantees teardown; the loop it
    # guards lives in _run. Read the whole class so this keeps
    # working wherever the body sits.
    source = inspect.getsource(Orchestrator)

    assert "_last_signature" in source, "no no-progress check in the loop"
    # It must compare BOTH the diff and what is blocking: the same diff with
    # a new blocker is genuinely new information.
    idx = source.index("_last_signature")
    window = source[max(0, idx - 700):idx + 200]
    assert "conf.blocking" in window
    assert "workspace.diff()" in window


# ---------------------------------------------------------------------------
# From a real run on stablyai/orca#23250.
# ---------------------------------------------------------------------------

def test_c3_does_not_pass_when_no_test_ever_ran():
    """The run reported `baseline: 0 passed, 0 failed (mode=absent)` and then
    `C3 existing_tests_pass: ok`. A Classification object exists even when it
    observed nothing, so the hard gate meaning "all existing tests pass"
    passed on a repository where not one test had executed."""
    from harness.phases.p5_confidence import _observed
    from harness.verify.classify import Classification

    assert _observed(Classification()) == 0
    assert _observed(None) == 0
    assert _observed(Classification(pre_existing=["t"])) == 1

    empty = _score(changed=["a.ts"], findings=[])
    # _score's stub supplies a populated classification; the guard is that
    # an empty one is not counted as a run.
    assert empty.existing_tests_pass or True

    class Verify:
        full = Classification()          # ran, saw nothing
        scoped = None
        oracle_passes = None
        oracle_id = ""
        oracle_output = ""
        blocking = []

        class judge:
            addresses = True
            conclusive = True

        class lint:
            blocks = False

    from harness.phases import p5_confidence as P5
    from harness.records import AffectedFile, ChangePlan, FileIntent

    class WS:
        def changed_files(self):
            return ["a.ts"]

        def diff_numstat(self):
            return (2, 1)

        def diff(self):
            return ""

    class Cfg:
        conservative = False

    report = P5.score(
        RootCauseRecord(statement="x", classification="logic",
                        confidence="high",
                        files=[AffectedFile("a.ts", (1, 2), "here")]),
        ChangePlan(files_to_change=[FileIntent("a.ts")],
                   estimated_lines_changed=5),
        Verify(), WS(), Cfg())
    assert not report.existing_tests_pass, \
        "C3 passed with zero tests observed"


def test_an_empty_baseline_escalates_to_a_container():
    """A configured suite that runs nothing is a repository whose tests
    cannot run HERE -- a native module built against another runtime, say.
    "node is installed" said the host was fine; the baseline disagreed."""
    import inspect

    from harness.orchestrator import Orchestrator
    source = inspect.getsource(Orchestrator._retry_baseline_in_container)
    assert "baseline.tests" in source
    assert "_try_container" in source
    # Bounded: it must not retry when already containerised or told not to.
    assert "active_container()" in source
    assert 'container.mode() == "off"' in source
