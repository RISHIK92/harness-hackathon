"""Permission for outward-facing actions, and the pull-request flow."""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harness import consent
from harness import pullrequest as PR
from harness.logging_ui import Logger
from harness.records import AffectedFile, RootCauseRecord


def silent():
    return Logger(stream=io.StringIO())


# -- policy ----------------------------------------------------------------
@pytest.mark.parametrize("raw,mode", [
    (None, consent.ASK), ("", consent.ASK), ("ask", consent.ASK),
    ("1", consent.AUTO), ("auto", consent.AUTO), ("yes", consent.AUTO),
    ("never", consent.NEVER), ("none", consent.NEVER),
    ("pr,push", consent.AUTO), ("comment", consent.AUTO),
])
def test_policy_parsing(raw, mode, monkeypatch):
    if raw is None:
        monkeypatch.delenv("HARNESS_AUTO", raising=False)
    else:
        monkeypatch.setenv("HARNESS_AUTO", raw)
    assert consent.Policy.from_env().mode == mode


def test_an_unrecognised_value_asks_rather_than_assuming_yes(monkeypatch):
    """Failing open on a typo would be the wrong direction."""
    monkeypatch.setenv("HARNESS_AUTO", "yse")
    assert consent.Policy.from_env().mode == consent.ASK


def test_a_named_subset_allows_only_those(monkeypatch):
    monkeypatch.setenv("HARNESS_AUTO", "push,pr")
    p = consent.Policy.from_env()
    assert p.allows(consent.PUSH) is True
    assert p.allows(consent.PR) is True
    assert p.allows(consent.COMMENT) is None      # still asks
    assert p.allows(consent.CLONE) is None


# -- the gate --------------------------------------------------------------
def test_default_asks_and_declining_is_respected(monkeypatch):
    monkeypatch.delenv("HARNESS_AUTO", raising=False)
    asked = []
    gate = consent.Gate(consent.Policy.from_env(), silent(),
                        confirm=lambda a, s, r: asked.append(a) or False)
    assert gate.allow(consent.PUSH, "push") is False
    assert asked == [consent.PUSH]


def test_unattended_refuses_what_was_not_asked_for(monkeypatch):
    """No one to ask means no, for anything the operator did not request."""
    monkeypatch.delenv("HARNESS_AUTO", raising=False)
    gate = consent.Gate(consent.Policy.from_env(), silent(), confirm=None)
    assert gate.allow(consent.PUSH, "push") is False
    assert gate.allow(consent.PR, "pr") is False
    assert gate.allow(consent.COMMENT, "comment") is False


def test_a_requested_action_proceeds_unattended(monkeypatch):
    """`make run ISSUE=owner/repo#1` IS the consent to clone owner/repo."""
    monkeypatch.delenv("HARNESS_AUTO", raising=False)
    gate = consent.Gate(consent.Policy.from_env(), silent(), confirm=None)
    assert gate.allow(consent.CLONE, "clone", requested=True) is True


def test_never_overrides_even_a_requested_action(monkeypatch):
    monkeypatch.setenv("HARNESS_AUTO", "never")
    gate = consent.Gate(consent.Policy.from_env(), silent(),
                        confirm=lambda *a: True)
    assert gate.allow(consent.CLONE, "clone", requested=True) is False


def test_decisions_are_recorded(monkeypatch):
    monkeypatch.setenv("HARNESS_AUTO", "push")
    gate = consent.Gate(consent.Policy.from_env(), silent(),
                        confirm=lambda *a: False)
    gate.allow(consent.PUSH, "x")
    gate.allow(consent.PR, "y")
    assert gate.decisions == [("push", "auto"), ("pr", "declined")]


def test_only_outward_facing_actions_exist():
    """Editing files and running tests are local and must not be gated.

    Installing is gated despite running locally, because it fetches code
    from a public registry and can execute a postinstall script from every
    transitive dependency -- the same class of act as pushing, not the same
    class as editing a file.
    """
    assert set(consent.OUTWARD) == {"clone", "push", "pr", "comment",
                                    "install"}
    for local in ("edit", "read", "grep", "test", "lint", "commit"):
        assert local not in consent.OUTWARD, (
            f"{local} is local and reversible; gating it would cost the "
            f"harness its autonomy without buying any safety")
    # Every gated action must be explainable to the operator.
    for action in consent.OUTWARD:
        assert consent.DESCRIBE.get(action), f"{action} has no description"


# -- pull request ----------------------------------------------------------
def rc(statement="parse_date indexes parts[0] without a guard"):
    return RootCauseRecord(statement=statement, classification="logic",
                           files=[AffectedFile("src/a.py", (1, 2), "here")])


class FakeIssue:
    title = "parse_date crashes"


def test_title_is_a_conventional_commit_subject():
    from harness.github import parse_ref
    title = PR.title_for(rc(), FakeIssue(), parse_ref("a/b#42"))
    assert title.startswith("fix: ")
    assert title.endswith("(#42)")
    assert len(title) < 90


def test_a_long_root_cause_is_truncated_on_a_word_boundary():
    long = "the parser " + "very " * 30 + "slowly fails"
    title = PR.title_for(rc(long), FakeIssue())
    assert len(title) <= 70
    assert not title.rstrip("…").endswith(" ")


def test_the_body_carries_the_report_and_closes_the_issue():
    from harness.github import parse_ref

    class S:
        cycles = 2
    body = PR.body_for("## 6. Verification\nall green", parse_ref("a/b#7"), S())
    assert "Closes #7." in body
    assert "## 6. Verification" in body
    assert "automated coding harness" in body
    assert "2 fix-and-verify cycle(s)" in body


def test_no_issue_means_no_closes_line():
    assert "Closes" not in PR.body_for("report", None, None)


# -- against a real local repository ---------------------------------------
def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n")
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["commit", "-qm", "init"],
                 ["remote", "add", "origin",
                  "https://github.com/owner/proj.git"],
                 ["checkout", "-q", "-b", "harness/issue-1"]):
        subprocess.run(["git", *args], cwd=repo, check=True,
                       capture_output=True, env=env)
    return repo


def test_remote_slug_and_branch(tmp_path):
    repo = make_repo(tmp_path)
    assert PR.remote_slug(repo) == "owner/proj"
    assert PR.current_branch(repo) == "harness/issue-1"


def test_nothing_to_commit_is_reported_not_pushed(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AUTO", raising=False)
    repo = make_repo(tmp_path)
    gate = consent.Gate(consent.Policy.from_env(), silent(),
                        confirm=lambda *a: True)
    result = PR.open_pr(repo, gate, silent(), rc(), FakeIssue(), "report")
    assert not result.ok
    assert "nothing to commit" in result.reason


def test_a_declined_push_commits_but_does_not_push(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AUTO", raising=False)
    repo = make_repo(tmp_path)
    (repo / "a.py").write_text("def f():\n    return 2\n")
    gate = consent.Gate(consent.Policy.from_env(), silent(),
                        confirm=lambda *a: False)
    result = PR.open_pr(repo, gate, silent(), rc(), FakeIssue(), "report")
    assert not result.ok
    assert "push was not allowed" in result.reason
    # the work is committed locally, so nothing is lost
    log = subprocess.run(["git", "log", "--oneline"], cwd=repo,
                         capture_output=True, text=True).stdout
    assert "fix:" in log


def test_a_non_github_remote_is_refused(tmp_path):
    repo = make_repo(tmp_path)
    subprocess.run(["git", "remote", "set-url", "origin",
                    "https://gitlab.com/o/p.git"], cwd=repo,
                   capture_output=True)
    gate = consent.Gate(consent.Policy(mode=consent.AUTO,
                                       only=set(consent.OUTWARD)), silent())
    result = PR.open_pr(repo, gate, silent(), rc(), FakeIssue(), "report")
    assert "not a GitHub remote" in result.reason


# ---------------------------------------------------------------------------
# A pull request is a claim that the change is good. It must be backed by the
# gates, not by the exit code.
#
# A real run on issue #2 turned `return 0` into `return "No data"`, broke the
# existing test, failed C2 and C3 -- and opened a pull request anyway,
# because PARTIAL was in the allowed set.
# ---------------------------------------------------------------------------

def _cfg(**kw):
    class C:
        pass
    c = C()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _conf(**overrides):
    from harness.records import ConfidenceReport
    base = dict(root_cause_evidenced=True, fix_addresses_root_cause=True,
                existing_tests_pass=True, no_unintended_changes=True,
                diff_proportional=True, external_factors_resolved=True)
    base.update(overrides)
    return ConfidenceReport(**base)


def _verify(new=(), oracle=None):
    from harness.verify.classify import Classification

    class V:
        full = Classification(new=list(new))
        scoped = None
        oracle_passes = oracle
    return V()


def test_a_successful_run_may_be_proposed():
    from harness import exits
    from harness.__main__ import _publishable
    assert _publishable(_cfg(_confidence=_conf(), _verify=_verify()),
                        exits.SUCCESS) == ""


def test_the_issue_2_run_would_no_longer_open_a_pull_request():
    """C2 and C3 failed and a test broke; a PR went out regardless."""
    from harness import exits
    from harness.__main__ import _publishable
    why = _publishable(
        _cfg(_confidence=_conf(fix_addresses_root_cause=False,
                               existing_tests_pass=False),
             _verify=_verify(new=["task completion rate"])),
        exits.PARTIAL)
    assert why, "a broken fix would still be proposed"
    assert "existing_tests_pass" in why


def test_a_new_test_failure_stops_the_pull_request():
    from harness import exits
    from harness.__main__ import _publishable
    why = _publishable(_cfg(_confidence=_conf(), _verify=_verify(new=["t1"])),
                       exits.PARTIAL)
    assert "new test failure" in why


def test_a_still_red_reproduction_stops_the_pull_request():
    from harness import exits
    from harness.__main__ import _publishable
    why = _publishable(_cfg(_confidence=_conf(), _verify=_verify(oracle=False)),
                       exits.PARTIAL)
    assert "reproduction test still fails" in why


def test_a_partial_failing_only_a_soft_condition_is_still_proposable():
    """Hard gates are the bar. A flagged style issue is not a reason to
    withhold a verified fix from a reviewer."""
    from harness import exits
    from harness.__main__ import _publishable
    assert _publishable(
        _cfg(_confidence=_conf(root_cause_evidenced=False),
             _verify=_verify()), exits.PARTIAL) == ""


def test_no_confidence_report_means_no_pull_request():
    from harness import exits
    from harness.__main__ import _publishable
    assert _publishable(_cfg(_confidence=None, _verify=None), exits.PARTIAL)


def test_a_failed_run_is_never_proposed():
    from harness import exits
    from harness.__main__ import _publishable
    for code in (exits.NO_FIX, exits.CONFIG_ERROR):
        assert _publishable(_cfg(_confidence=_conf(), _verify=_verify()), code)


def test_the_console_menu_applies_the_same_bar():
    """Choosing "open a pull request" from a menu is not evidence either."""
    import inspect

    from harness import __main__ as M
    source = inspect.getsource(M._interactive)
    assert "_publishable(_cfg, _last)" in source, \
        "the interactive path can still propose a broken fix"
