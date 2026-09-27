"""The things a general coding agent does that this harness could not.

Each test here stands for a task the harness used to hand back: a cloned repo
whose suite cannot start, a fix that needs a new module, a dead file to
remove, a weak root cause nobody looked at twice, an issue too vague to act
on with a human sitting right there.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.edit.apply import apply_all, rollback                  # noqa: E402
from harness.edit.formats import WHOLE_FILE                         # noqa: E402
from harness.edit.parse import parse                                # noqa: E402
from harness.verify import deps                                     # noqa: E402


class Log:
    def __getattr__(self, _):
        return lambda *a, **k: None


class Gate:
    def __init__(self, allow=True):
        self.answer = allow
        self.asked = []

    def allow(self, action, summary, rows=None, requested=False):
        self.asked.append(action)
        return self.answer


class TC:
    def __init__(self, language, framework="", test_cmd=None):
        self.language = language
        self.framework = framework
        self.test_cmd = test_cmd


# -- 1. dependencies -------------------------------------------------------

def test_a_fresh_clone_with_no_node_modules_needs_installing(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"name":"x","scripts":{"test":"jest"},"devDependencies":{"jest":"^29"}}')
    plan = deps.assess(tmp_path, TC("javascript"))
    assert plan.needed
    assert plan.cmd == "npm install --ignore-scripts"


def test_install_scripts_are_disabled_unless_asked_for(tmp_path, monkeypatch):
    """`npm install` runs postinstall from every transitive dependency."""
    (tmp_path / "package.json").write_text('{"name":"x"}')
    assert "--ignore-scripts" in deps.node_command(tmp_path)

    monkeypatch.setenv("HARNESS_INSTALL_SCRIPTS", "1")
    assert "--ignore-scripts" not in deps.node_command(tmp_path)


def test_a_lockfile_selects_a_reproducible_install(tmp_path):
    """The lockfile picks the manager. The command may be prefixed with
    `corepack` when that manager is not installed, which is the point of
    the prefix -- so assert on the manager, not on the first word."""
    (tmp_path / "package.json").write_text('{"name":"x"}')
    (tmp_path / "package-lock.json").write_text("{}")
    assert "npm ci" in deps.node_command(tmp_path)

    (tmp_path / "package-lock.json").unlink()
    (tmp_path / "yarn.lock").write_text("")
    assert "yarn install" in deps.node_command(tmp_path)

    (tmp_path / "yarn.lock").unlink()
    (tmp_path / "pnpm-lock.yaml").write_text("")
    assert "pnpm install" in deps.node_command(tmp_path)


def test_a_missing_package_manager_falls_back_to_corepack(tmp_path,
                                                          monkeypatch):
    """Repositories pin a manager nobody has installed. Node ships corepack
    for exactly this, and it fetches the pinned version -- without it the
    suite simply never runs."""
    import shutil
    real = shutil.which
    monkeypatch.setattr(shutil, "which",
                        lambda t: None if t == "pnpm" else real(t))
    (tmp_path / "package.json").write_text('{"name":"x"}')
    (tmp_path / "pnpm-lock.yaml").write_text("")
    cmd = deps.node_command(tmp_path)
    assert cmd.startswith("corepack pnpm") or not real("corepack")


def test_npm_is_never_routed_through_corepack(tmp_path, monkeypatch):
    """npm ships with node; a corepack shim for it would be pointless."""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda t: None)
    assert deps._runner("npm") == "npm"


def test_a_repository_that_already_has_its_dependencies_is_left_alone(tmp_path):
    (tmp_path / "package.json").write_text('{"name":"x"}')
    (tmp_path / "node_modules" / "jest").mkdir(parents=True)
    assert not deps.assess(tmp_path, TC("javascript")).needed


def test_an_empty_node_modules_still_counts_as_missing(tmp_path):
    (tmp_path / "package.json").write_text('{"name":"x"}')
    (tmp_path / "node_modules").mkdir()
    assert deps.assess(tmp_path, TC("javascript")).needed


def test_installing_is_refused_when_the_gate_says_no(tmp_path):
    (tmp_path / "package.json").write_text('{"name":"x"}')
    gate = Gate(allow=False)
    plan = deps.ensure(tmp_path, TC("javascript"), gate, Log())

    from harness import consent
    assert gate.asked == [consent.INSTALL]
    assert not plan.ran, "installed without permission"
    assert plan.reason == "declined"


def test_install_stays_on_the_model_deny_list():
    """The harness issues it directly; the model still may not."""
    from harness.verify.runner import CommandRefused, check_allowed
    for cmd in ("npm install", "pip install requests"):
        with pytest.raises(CommandRefused):
            check_allowed(cmd)


# -- 2, 3. creating, deleting and moving files -----------------------------

REPLY = """<<<<<<< CREATE src/helper.py
def helper():
    return 42
>>>>>>>

<<<<<<< DELETE src/dead.py
>>>>>>>

<<<<<<< RENAME src/old.py -> src/new.py
>>>>>>>"""


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "dead.py").write_text("# unused\n")
    (tmp_path / "src" / "old.py").write_text("VALUE = 1\n")
    return tmp_path


def test_a_fix_can_add_remove_and_move_files(repo):
    applied = apply_all(repo, parse(REPLY, WHOLE_FILE, default_path="x.py"))

    assert (repo / "src/helper.py").read_text() == \
        "def helper():\n    return 42\n"
    assert not (repo / "src/dead.py").exists()
    assert not (repo / "src/old.py").exists()
    assert (repo / "src/new.py").read_text() == "VALUE = 1\n"
    assert {a.op for a in applied} == {"create", "delete", "rename"}


def test_rollback_undoes_every_kind_of_change(repo):
    applied = apply_all(repo, parse(REPLY, WHOLE_FILE, default_path="x.py"))
    rollback(repo, applied)

    assert not (repo / "src/helper.py").exists(), "created file survived"
    assert (repo / "src/dead.py").read_text() == "# unused\n"
    assert (repo / "src/old.py").read_text() == "VALUE = 1\n"
    assert not (repo / "src/new.py").exists()


def test_creating_over_an_existing_file_is_refused(repo):
    from harness.edit.apply import EditFailure
    reply = "<<<<<<< CREATE src/old.py\nx = 2\n>>>>>>>"
    with pytest.raises(EditFailure) as exc:
        apply_all(repo, parse(reply, WHOLE_FILE, default_path="x.py"))
    assert "already exists" in str(exc.value)
    assert (repo / "src/old.py").read_text() == "VALUE = 1\n", "clobbered"


def test_renaming_onto_an_existing_file_is_refused(repo):
    from harness.edit.apply import EditFailure
    reply = "<<<<<<< RENAME src/old.py -> src/dead.py\n>>>>>>>"
    with pytest.raises(EditFailure):
        apply_all(repo, parse(reply, WHOLE_FILE, default_path="x.py"))
    assert (repo / "src/old.py").exists()


def test_a_new_file_may_be_edited_in_the_same_reply(repo):
    reply = ("<<<<<<< CREATE src/fresh.py\nA = 1\n>>>>>>>\n\n"
             "<<<<<<< SEARCH src/fresh.py\nA = 1\n=======\nA = 2\n"
             ">>>>>>> REPLACE")
    from harness.edit.formats import SEARCH_REPLACE
    apply_all(repo, parse(reply, SEARCH_REPLACE, default_path="src/fresh.py"))
    assert (repo / "src/fresh.py").read_text().strip() == "A = 2"


def test_a_new_file_must_still_be_in_the_plan(repo):
    """Creating a file is not a way around the scope gate."""
    from harness.edit.validate import scope_check
    from harness.edit.apply import EditFailure
    from harness.records import ChangePlan, FileIntent

    applied = apply_all(repo, parse("<<<<<<< CREATE src/sneaky.py\nx=1\n>>>>>>>",
                                    WHOLE_FILE, default_path="x.py"))
    plan = ChangePlan(files_to_change=[FileIntent("src/old.py")])
    with pytest.raises(EditFailure):
        scope_check(applied, plan)


def test_a_rename_destination_is_scope_checked(repo):
    from harness.edit.validate import scope_check
    from harness.edit.apply import EditFailure
    from harness.records import ChangePlan, FileIntent

    applied = apply_all(repo, parse("<<<<<<< RENAME src/old.py -> src/moved.py"
                                    "\n>>>>>>>", WHOLE_FILE,
                                    default_path="x.py"))
    plan = ChangePlan(files_to_change=[FileIntent("src/old.py")])
    with pytest.raises(EditFailure) as exc:
        scope_check(applied, plan)
    assert "src/moved.py" in str(exc.value), \
        "a file could be moved anywhere the plan never named"


def test_only_plausible_new_paths_may_be_planned():
    from harness.phases.p2_scope import _plausible_new_path as ok
    assert ok("src/new_helper.py")
    assert ok("src/lib/util.js")
    assert not ok("/etc/passwd")
    assert not ok("../outside.py")
    assert not ok("package-lock.json")
    assert not ok("tests/test_thing.py"), "tests stay off the allow list"


def test_file_operations_are_only_offered_when_planned():
    """Otherwise a model creates a file instead of making a small edit."""
    from harness.phases.p3_implement import _plan_has_new_files
    from harness.records import ChangePlan, FileIntent

    assert not _plan_has_new_files(ChangePlan(
        files_to_change=[FileIntent("a.py")]))
    assert _plan_has_new_files(ChangePlan(
        files_to_change=[FileIntent("a.py", is_new=True)]))


# -- 4. looking again ------------------------------------------------------

def test_the_follow_up_round_is_bounded(monkeypatch):
    from harness.phases.p1_investigate import _extra_rounds

    monkeypatch.delenv("HARNESS_EXPLORE_ROUNDS", raising=False)
    assert _extra_rounds() == 1, "looks again once by default"
    monkeypatch.setenv("HARNESS_EXPLORE_ROUNDS", "0")
    assert _extra_rounds() == 0
    monkeypatch.setenv("HARNESS_EXPLORE_ROUNDS", "9")
    assert _extra_rounds() == 3, "a vague issue must not eat the budget"
    monkeypatch.setenv("HARNESS_EXPLORE_ROUNDS", "banana")
    assert _extra_rounds() == 1, "a typo falls back to the default"


# -- 5. asking -------------------------------------------------------------

def _issue(text, files=frozenset()):
    from harness.phases import p0_triage
    return p0_triage.triage(text, set(files))


def test_a_vague_issue_is_asked_about_when_someone_is_there(tmp_path):
    from harness.orchestrator import Orchestrator
    from harness.config import Config

    cfg = Config(api_key="sk-ant-x", issue="it is broken", repo_path=tmp_path)
    asked = []

    def clarify(question, why=""):
        asked.append(question)
        return "look at parse_date in src/dateparse/parser.py"

    orch = Orchestrator(cfg, Log(), clarify=clarify)
    issue = _issue(cfg.issue)
    assert issue.vagueness >= 0.7, "fixture is not actually vague"

    orch._clarify_if_vague(issue, set())
    assert asked, "a human was present and was never asked"
    assert "parse_date" in cfg.issue, "the answer was not used"


def test_nothing_is_asked_when_nobody_is_there(tmp_path):
    """Unattended it still declines rather than inventing an answer."""
    from harness.orchestrator import Orchestrator
    from harness.config import Config

    cfg = Config(api_key="sk-ant-x", issue="it is broken", repo_path=tmp_path)
    before = cfg.issue
    orch = Orchestrator(cfg, Log(), clarify=None)
    orch._clarify_if_vague(_issue(cfg.issue), set())
    assert cfg.issue == before


def test_a_grounded_issue_is_never_interrupted(tmp_path):
    from harness.orchestrator import Orchestrator
    from harness.config import Config

    text = "parse_date in src/dateparse/parser.py raises IndexError"
    cfg = Config(api_key="sk-ant-x", issue=text, repo_path=tmp_path)
    asked = []
    orch = Orchestrator(cfg, Log(),
                        clarify=lambda q, why="": asked.append(q))
    orch._clarify_if_vague(_issue(text, {"src/dateparse/parser.py"}), set())
    assert not asked, "interrupted a run that had everything it needed"
