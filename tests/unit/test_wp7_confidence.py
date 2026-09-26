"""WP7: confidence conditions, the decision table, recovery, best-attempt."""
from __future__ import annotations

import io
import itertools
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harness import config as C
from harness.logging_ui import Logger
from harness.phases.p5_confidence import REMEDY_PHASE, Action, decide, score
from harness.recovery import alternatives, stuck, taxonomy
from harness.records import (AffectedFile, ChangePlan, Check, ConfidenceReport,
                             Evidence, FileIntent, Hypothesis, RootCauseRecord)


def report(**kw) -> ConfidenceReport:
    r = ConfidenceReport()
    for k, v in kw.items():
        setattr(r, k, v)
    return r


# -- T7.2: the exhaustive guarantee (FR-35) --------------------------------
def test_no_combination_with_a_failed_hard_gate_ever_submits():
    """64 combinations. A silent submission is the worst possible failure:
    it looks like success."""
    submits = 0
    for bits in itertools.product([False, True], repeat=6):
        r = report(**dict(zip(ConfidenceReport.CONDITIONS, bits)))
        action = decide(r, cycle=1, max_cycles=5)
        if action is Action.SUBMIT:
            submits += 1
            assert all(getattr(r, c) for c in ConfidenceReport.HARD), bits
            assert all(bits), f"SUBMIT with a failed condition: {bits}"
    assert submits == 1, "exactly one combination may submit: all six true"


def test_every_non_submit_action_has_a_remedy_phase():
    for bits in itertools.product([False, True], repeat=6):
        r = report(**dict(zip(ConfidenceReport.CONDITIONS, bits)))
        action = decide(r, 1, 5)
        if action is not Action.SUBMIT:
            assert action in REMEDY_PHASE, action


def test_hard_gates_are_checked_before_soft_ones():
    r = report(root_cause_evidenced=False, fix_addresses_root_cause=False,
               existing_tests_pass=True, no_unintended_changes=False,
               diff_proportional=True, external_factors_resolved=True)
    assert decide(r, 1, 5) is Action.REVERT_AND_REIMPLEMENT


def test_inconclusive_judge_does_not_force_reinvestigation():
    """An unreliable judge must not veto an otherwise-green run."""
    r = report(root_cause_evidenced=True, fix_addresses_root_cause=False,
               existing_tests_pass=True, no_unintended_changes=True,
               diff_proportional=True, external_factors_resolved=True,
               judge_conclusive=False)
    assert decide(r, 1, 5) is Action.SUBMIT


@pytest.mark.parametrize("failed,expected", [
    ("no_unintended_changes", Action.REVERT_AND_REIMPLEMENT),
    ("diff_proportional", Action.RESCOPE),
    ("existing_tests_pass", Action.REIMPLEMENT_TARGETED),
    ("root_cause_evidenced", Action.REINVESTIGATE),
    ("external_factors_resolved", Action.REINVESTIGATE),
])
def test_each_condition_routes_to_its_remedy(failed, expected):
    bits = {c: True for c in ConfidenceReport.CONDITIONS}
    bits[failed] = False
    assert decide(report(**bits), 1, 5) is expected


# -- T7.1 scoring from evidence --------------------------------------------
class FakeWorkspace:
    def __init__(self, changed, added=3, removed=1):
        self._changed, self._a, self._r = changed, added, removed

    def changed_files(self):
        return list(self._changed)

    def diff_numstat(self):
        return (self._a, self._r)


class FakeCls:
    def __init__(self, blocking=(), fixed=(), pre=()):
        self.blocking = list(blocking)
        self.fixed = list(fixed)
        self.pre_existing = list(pre)


class FakeVerify:
    def __init__(self, **kw):
        self.scoped = kw.get("scoped")
        self.full = kw.get("full")
        self.oracle_passes = kw.get("oracle_passes")
        self.judge = kw.get("judge")
        self.lint = kw.get("lint")

    @property
    def blocking(self):
        out = []
        for c in (self.scoped, self.full):
            if c:
                out += c.blocking
        return out


class FakeJudge:
    def __init__(self, addresses=True, conclusive=True):
        self.addresses, self.conclusive = addresses, conclusive


def make_cfg(tmp_path, conservative=False):
    cfg = C.Config(api_key="k", issue="x", repo_path=tmp_path)
    cfg.conservative = conservative
    return cfg


def base_records():
    rc = RootCauseRecord(
        statement="parse_date indexes parts[0] without a guard",
        classification="logic", confidence="high",
        evidence=[Evidence("sbfl", "parser.py:11 suspiciousness 0.71")],
        files=[AffectedFile("src/a.py", (10, 12), "here")],
        external_factors={"ruled_out": True})
    plan = ChangePlan(fix_description="guard the split",
                      files_to_change=[FileIntent("src/a.py")],
                      estimated_lines_changed=4)
    return rc, plan


def test_all_six_green(tmp_path):
    rc, plan = base_records()
    v = FakeVerify(full=FakeCls(fixed=["t1"]), judge=FakeJudge(),
                   oracle_passes=True)
    r = score(rc, plan, v, FakeWorkspace(["src/a.py"], 3, 1),
              make_cfg(tmp_path))
    assert r.score == 6 and r.overall == "HIGH"
    assert decide(r, 1, 5) is Action.SUBMIT


def test_c2_accepts_a_fixed_test_over_a_negative_judge(tmp_path):
    """Hard evidence outranks the judge."""
    rc, plan = base_records()
    v = FakeVerify(full=FakeCls(fixed=["t1"]),
                   judge=FakeJudge(addresses=False))
    r = score(rc, plan, v, FakeWorkspace(["src/a.py"]), make_cfg(tmp_path))
    assert r.fix_addresses_root_cause is True


def test_c4_catches_an_unplanned_file(tmp_path):
    rc, plan = base_records()
    v = FakeVerify(full=FakeCls(), judge=FakeJudge())
    r = score(rc, plan, v, FakeWorkspace(["src/a.py", "src/other.py"]),
              make_cfg(tmp_path))
    assert r.no_unintended_changes is False
    assert decide(r, 1, 5) is Action.REVERT_AND_REIMPLEMENT


def test_c5_catches_an_oversized_diff(tmp_path):
    rc, plan = base_records()
    v = FakeVerify(full=FakeCls(), judge=FakeJudge())
    r = score(rc, plan, v, FakeWorkspace(["src/a.py"], 40, 5),
              make_cfg(tmp_path))
    assert r.diff_proportional is False


def test_c5_is_tighter_in_conservative_mode(tmp_path):
    rc, plan = base_records()
    plan.estimated_lines_changed = 100
    v = FakeVerify(full=FakeCls(), judge=FakeJudge())
    r = score(rc, plan, v, FakeWorkspace(["src/a.py"], 16, 0),
              make_cfg(tmp_path, conservative=True))
    assert r.diff_proportional is False


def test_c1_requires_executed_evidence(tmp_path):
    rc, plan = base_records()
    rc.evidence = []
    v = FakeVerify(full=FakeCls(), judge=FakeJudge())
    r = score(rc, plan, v, FakeWorkspace(["src/a.py"]), make_cfg(tmp_path))
    assert r.root_cause_evidenced is False


def test_c3_requires_tests_to_have_run(tmp_path):
    """No test run means unverified, not 'passing'."""
    rc, plan = base_records()
    v = FakeVerify(judge=FakeJudge())
    r = score(rc, plan, v, FakeWorkspace(["src/a.py"]), make_cfg(tmp_path))
    assert r.existing_tests_pass is False


def test_c3_ignores_pre_existing_failures(tmp_path):
    rc, plan = base_records()
    v = FakeVerify(full=FakeCls(pre=["old1", "old2"]), judge=FakeJudge())
    r = score(rc, plan, v, FakeWorkspace(["src/a.py"]), make_cfg(tmp_path))
    assert r.existing_tests_pass is True


# -- T7.4 taxonomy ---------------------------------------------------------
@pytest.mark.parametrize("text,kind", [
    ("ERROR collecting tests/test_x.py", taxonomy.COLLECTION_ERROR),
    ("ModuleNotFoundError: No module named 'requests'",
     taxonomy.COLLECTION_ERROR),
    ("KeyError: 'MAILER_ENDPOINT'", taxonomy.ENVIRONMENT_ERROR),
    ("TypeError: got an unexpected keyword argument", taxonomy.TYPE_ERROR),
    ("[timed out after 120s]", taxonomy.COMMAND_TIMEOUT),
])
def test_failure_text_classification(text, kind):
    assert taxonomy.classify_text(text).kind == kind


def test_every_kind_has_a_deterministic_first_move():
    for kind in taxonomy.RECOVERY:
        f = taxonomy._make(kind, "detail")
        assert f.first_move and f.phase in ("P1", "P2", "P3", "P4", "P5")


def test_edit_failure_classification():
    class E:
        stage = "locate"
        detail = "not found"
    assert taxonomy.classify_edit_failure(E()).kind == taxonomy.PATCH_NOT_APPLIED


# -- T7.5 stuck detection --------------------------------------------------
def test_same_command_three_times_is_stuck():
    s = stuck.StuckState()
    for _ in range(3):
        s.command("pytest -q", "1 failed")
    assert s.check() == "same command and output"


def test_same_failure_signature_ignores_line_numbers():
    s = stuck.StuckState()
    for n in (10, 20, 30):
        s.failure(f"AssertionError at src/a.py:{n}")
    assert s.check() == "same failure signature"


def test_identical_diff_twice_is_stuck():
    s = stuck.StuckState()
    diff = "--- a\n+++ b\n+    return 1\n-    return 0\n"
    s.diff(diff)
    s.diff(diff.replace("+    return 1", "+     return  1"))   # whitespace
    assert s.check() == "identical diff already rejected"


def test_no_new_observation_is_stuck():
    s = stuck.StuckState()
    s.observe(["a.py"])
    for _ in range(4):
        s.observe(["a.py"])
    assert s.check() == "no new file, symbol or test observed"


def test_progress_resets_the_barren_counter():
    s = stuck.StuckState()
    for i in range(3):
        s.observe(["a.py"])
    s.observe(["b.py"])
    assert s.check() is None


def test_escalation_ladder():
    assert stuck.escalate("P3") == "P2"
    assert stuck.escalate("P2") == "P1"
    assert stuck.escalate("P1") == "P1"


# -- T7.6 rejected alternatives --------------------------------------------
def test_next_alternative_prefers_the_best_supported_untried():
    rc, _ = base_records()
    rc.hypotheses = [
        Hypothesis("H1", "first", check=Check("grep", "a"), support="refuted"),
        Hypothesis("H2", "second", check=Check("grep", "b"),
                   support="inconclusive"),
        Hypothesis("H3", "third", check=Check("grep", "c"), support="untested"),
    ]
    nxt = alternatives.next_alternative(rc, tried={"H1"})
    assert nxt.id == "H2"
    assert "H2" in alternatives.describe(nxt)


def test_refuted_alternatives_are_never_reused():
    rc, _ = base_records()
    rc.hypotheses = [
        Hypothesis("H1", "a", check=Check("grep", "a"), support="refuted"),
        Hypothesis("H2", "b", check=Check("grep", "b"), support="refuted"),
    ]
    assert alternatives.next_alternative(rc, tried=set()) is None


def test_exhausted_alternatives_return_none():
    rc, _ = base_records()
    rc.hypotheses = [Hypothesis("H1", "a", check=Check("grep", "a"),
                                support="confirmed")]
    assert alternatives.next_alternative(rc, tried={"H1"}) is None


# -- T7.3 best-attempt selection -------------------------------------------
def test_best_attempt_prefers_fewer_new_failures():
    from harness.orchestrator import Orchestrator
    attempts = [
        {"cycle": 3, "new_failures": 2, "judge_ok": True, "hygiene": 0,
         "diff_lines": 4},
        {"cycle": 5, "new_failures": 3, "judge_ok": True, "hygiene": 0,
         "diff_lines": 2},
    ]
    assert Orchestrator._best(attempts)["cycle"] == 3


def test_best_attempt_tie_breaks_on_judge_then_size():
    from harness.orchestrator import Orchestrator
    attempts = [
        {"cycle": 1, "new_failures": 0, "judge_ok": False, "hygiene": 0,
         "diff_lines": 2},
        {"cycle": 2, "new_failures": 0, "judge_ok": True, "hygiene": 0,
         "diff_lines": 9},
        {"cycle": 3, "new_failures": 0, "judge_ok": True, "hygiene": 0,
         "diff_lines": 4},
    ]
    assert Orchestrator._best(attempts)["cycle"] == 3


def test_no_attempts_yields_none():
    from harness.orchestrator import Orchestrator
    assert Orchestrator._best([]) is None
