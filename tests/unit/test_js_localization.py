"""The run that found the bug and changed nothing.

A real run on a JavaScript repository identified the root cause, then spent
four of five cycles refusing to implement it, and reported NO_FIX without
saying why. Every localization signal had come back empty:

  * SBFL needs coverage, which is instrumented for Python only;
  * the lexical signal greps the issue's identifiers -- and the issue said
    "bucket", a word that appears nowhere in the source (`projectBreakdown`,
    `onTrack`, `atRisk`), only in the test names;
  * so the plan named no file, and P3 refused it. Four times, identically.

The harness knew which tests were failing the whole time. A test file
imports what it tests.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.localize import from_tests as FT            # noqa: E402
from harness.phases.p2_scope import harden               # noqa: E402
from harness.records import (ChangePlan, FileIntent,  # noqa: E402
                             RootCauseRecord)
from harness.repo.search import Search                   # noqa: E402

FAILING = ["every project lands in exactly one bucket",
           "finished projects are reported as completed",
           "bucket boundaries"]


@pytest.fixture
def js_repo(tmp_path):
    """An ESM project whose test imports the module under test."""
    (tmp_path / "src" / "lib").mkdir(parents=True)
    (tmp_path / "test").mkdir()
    (tmp_path / "package.json").write_text('{"name":"m","type":"module"}')
    (tmp_path / "src" / "lib" / "dashboardStats.js").write_text(
        "export function projectBreakdown(projects) {\n"
        "  const onTrack = projects.filter((p) => p.progress >= 50).length;\n"
        "  return { total: projects.length, onTrack };\n}\n")
    (tmp_path / "test" / "dashboardStats.test.js").write_text(
        "import { test } from 'node:test';\n"
        "import assert from 'node:assert/strict';\n"
        "import { projectBreakdown } from '../src/lib/dashboardStats.js';\n"
        "test('every project lands in exactly one bucket', () => {});\n"
        "test('finished projects are reported as completed', () => {});\n"
        "test('bucket boundaries', () => {});\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)
    return tmp_path


def test_the_failing_tests_point_at_the_code_under_test(js_repo):
    found = FT.candidates(js_repo, Search(js_repo), FAILING)
    assert found == ["src/lib/dashboardStats.js"], \
        "the one signal that works without coverage found nothing"


def test_a_test_file_that_imports_nothing_local_says_nothing(js_repo):
    """No guessing: an unhelpful signal returns empty, not a shrug."""
    (js_repo / "test" / "dashboardStats.test.js").write_text(
        "import { test } from 'node:test';\n"
        "test('bucket boundaries', () => {});\n")
    assert FT.candidates(js_repo, Search(js_repo), FAILING) == []


def test_python_imports_resolve_too(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "calc.py").write_text("def total():\n    return 1\n")
    (tmp_path / "test_calc.py").write_text(
        "from pkg.calc import total\n\n"
        "def test_totals_add_up():\n    assert total() == 2\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "i"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)
    found = FT.candidates(tmp_path, Search(tmp_path), ["test_totals_add_up"])
    assert "pkg/calc.py" in found


class _Cfg:
    conservative = True


class _Log:
    def __getattr__(self, _):
        return lambda *a, **k: None


class _Ctx:
    def __init__(self, repo):
        self.repo = repo
        self.search = Search(repo)
        self.log = _Log()
        self.cfg = _Cfg()
        self.degradations = []

    def degraded(self, tag, detail=""):
        self.degradations.append(tag)


def test_the_plan_is_never_left_empty_when_a_candidate_exists(js_repo):
    """The exact shape of the failing run: the model named Python paths that
    do not exist here, and the root cause carried no file at all."""
    plan = ChangePlan(
        fix_description="classify 90%+ as completed",
        files_to_change=[],
        files_must_not_change=["src/dashboard/__init__.py",
                               "src/dashboard/models.py", "tests/"],
        estimated_lines_changed=5)
    root_cause = RootCauseRecord(statement="90%+ unaccounted for",
                                 confidence="low", files=[])

    ctx = _Ctx(js_repo)
    harden(ctx, plan, root_cause,
           candidates=FT.candidates(js_repo, ctx.search, FAILING))

    assert [f.path for f in plan.files_to_change] == \
        ["src/lib/dashboardStats.js"], "P3 would refuse this plan again"
    assert "no_target" not in ctx.degradations


def test_paths_that_do_not_exist_here_are_dropped_from_the_deny_list(js_repo):
    """A must-not-change list full of another language's paths makes the
    report look like the harness understands a repository it does not."""
    plan = ChangePlan(files_to_change=[],
                      files_must_not_change=["src/dashboard/models.py",
                                             "src/dashboard/__init__.py",
                                             "tests/"])
    ctx = _Ctx(js_repo)
    harden(ctx, plan, RootCauseRecord(statement="x", files=[]),
           candidates=FT.candidates(js_repo, ctx.search, FAILING))
    assert not any("dashboard/" in p for p in plan.files_must_not_change)


def test_with_no_candidate_at_all_the_run_says_so(js_repo):
    """Still no guessing -- but it must be recorded, not silent."""
    ctx = _Ctx(js_repo)
    plan = ChangePlan(files_to_change=[], files_must_not_change=[])
    harden(ctx, plan, RootCauseRecord(statement="x", files=[]), candidates=[])
    assert plan.files_to_change == []
    assert "no_target" in ctx.degradations, \
        "an empty plan has to be reported, not discovered in a trajectory"


def test_the_report_states_why_nothing_happened():
    """The run report opened with NO_FIX and never said what stopped it."""
    import inspect
    from harness import report
    source = inspect.getsource(report.build)
    assert "summary.reason" in source, "the reason is still not in the report"
    assert "Nothing was changed" in source


# ---------------------------------------------------------------------------
# Large repositories. Found on a real 30k-file repo, where the harness was
# navigating by an index holding 15% of the tree and never said so.
# ---------------------------------------------------------------------------

def test_the_file_index_is_never_truncated(tmp_path):
    """The runner caps output at 250 KB to protect the model's context.
    `git ls-files` on a large repo is far bigger, so the index silently held
    the alphabetically first few thousand paths -- and everything after them
    was invisible to every phase."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)
    # Long names so the listing comfortably exceeds the cap.
    stem = "z" * 120
    for i in range(3000):
        (tmp_path / f"{stem}{i:05d}.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)

    files = Search(tmp_path).files()
    assert len(files) == 3000, f"index holds {len(files)} of 3000"


def test_changed_files_is_never_truncated():
    """C4 checks this list for unintended changes; a cut-off list hides one."""
    import inspect
    from harness.repo.workspace import Workspace
    source = inspect.getsource(Workspace.changed_files)
    assert "truncate=False" in source


def test_a_path_inside_a_github_url_resolves(tmp_path):
    """Issues link to code rather than quoting it, and the blob URL carries
    `github.com/owner/repo/blob/<sha>/` in front of the path."""
    from harness.phases.p0_triage import _resolve_path

    known = {"src/relay/pty-handler.ts", "src/shared/other.ts"}
    url = ("github.com/stablyai/orca/blob/a85057ef4802fb2dbeec7daba17ad199"
           "/src/relay/pty-handler.ts")
    assert _resolve_path(url, known) == ["src/relay/pty-handler.ts"]


def test_an_ambiguous_tail_is_not_guessed_at():
    """`utils.ts` in forty places is not a reference to any one of them."""
    from harness.phases.p0_triage import _resolve_path

    known = {f"src/a{i}/utils.ts" for i in range(5)}
    assert _resolve_path("some/url/prefix/utils.ts", known) == []


def test_the_canonical_test_command_beats_a_ci_variant():
    """A CI workflow holds many specialised invocations; taking the first
    match chose a profiling run over the repository's own test script."""
    from harness.verify.toolchain import _pick

    cmds = ["pnpm test:bun:profile --artifact", "pnpm test",
            "pnpm test:e2e --headed"]
    assert _pick(cmds, ("test",)) == "pnpm test"


def test_budgets_grow_with_the_repository():
    """The 25-minute default was set against fixtures of a few dozen files.
    On a 30k-file repo it is the fixture's limit applied to something else:
    installing 137 dependencies and running a real suite does not fit."""
    from harness.budget import Budgets

    class Cfg:
        token_budget = 900_000
        time_budget = 1500

    def scaled(n):
        b = Budgets.from_config(Cfg())
        b.scale_to_repo(n)
        return b.clock.limit_s, b.tokens.total

    assert scaled(50) == (1500, 900_000), "a small repo must be untouched"
    assert scaled(900) == (1500, 900_000)

    big_time, big_tokens = scaled(29_864)
    assert big_time > 1500 * 3
    assert big_tokens > 900_000 * 2

    # Monotonic: a larger repository never gets a smaller budget.
    sizes = [50, 900, 2_000, 8_000, 29_864]
    times = [scaled(n)[0] for n in sizes]
    assert times == sorted(times)


def test_an_explicit_budget_is_never_overridden(monkeypatch):
    """Naming a budget is a decision, not a default to improve on."""
    import inspect

    from harness.orchestrator import Orchestrator
    # run() is a thin wrapper that guarantees teardown; the loop it
    # guards lives in _run. Read the whole class so this keeps
    # working wherever the body sits.
    source = inspect.getsource(Orchestrator)
    assert "HARNESS_TIME_BUDGET" in source and "scale_to_repo" in source
    idx = source.index("scale_to_repo")
    assert "if not (os.environ.get" in source[max(0, idx - 300):idx]


# ---------------------------------------------------------------------------
# From the orca run: the model diagnosed the bug exactly, then scoping
# targeted a CI helper the issue never mentions.
# ---------------------------------------------------------------------------

class _Anchors:
    def __init__(self, files):
        self.files = list(files)
        self.symbols = []
        self.errors = []
        self.frames = []


class _Issue:
    def __init__(self, files):
        self.anchors = _Anchors(files)


def test_a_plan_touching_nothing_the_issue_names_is_steered_back(tmp_path):
    """`.github/actions/.../storage-lease.mjs` exists, so the "drop paths
    that do not exist" filter passed it, and the empty-plan fallback never
    fired because the plan was not empty -- just wrong."""
    (tmp_path / "src" / "relay").mkdir(parents=True)
    (tmp_path / "src" / "relay" / "pty-handler.ts").write_text("const x = 1\n")
    (tmp_path / ".github" / "actions").mkdir(parents=True)
    (tmp_path / ".github" / "actions" / "lease.mjs").write_text("export {}\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)

    plan = ChangePlan(files_to_change=[FileIntent(".github/actions/lease.mjs")],
                      estimated_lines_changed=10)
    ctx = _Ctx(tmp_path)
    harden(ctx, plan,
           RootCauseRecord(statement="LF instead of CR", files=[]),
           candidates=[], issue=_Issue(["src/relay/pty-handler.ts"]))

    assert [f.path for f in plan.files_to_change] == \
        ["src/relay/pty-handler.ts"], "kept a plan the issue never supports"
    assert "off_target" in ctx.degradations


def test_a_plan_the_issue_supports_is_left_alone(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("x\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)

    plan = ChangePlan(files_to_change=[FileIntent("src/a.ts")],
                      estimated_lines_changed=10)
    ctx = _Ctx(tmp_path)
    harden(ctx, plan, RootCauseRecord(statement="x", files=[]),
           candidates=[], issue=_Issue(["src/a.ts"]))
    assert [f.path for f in plan.files_to_change] == ["src/a.ts"]
    assert "off_target" not in ctx.degradations


def test_a_vague_issue_does_not_steer_anything(tmp_path):
    """With no anchors the model's choice is all there is; overriding it
    would be substituting one guess for another."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("x\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)

    plan = ChangePlan(files_to_change=[FileIntent("src/a.ts")],
                      estimated_lines_changed=10)
    ctx = _Ctx(tmp_path)
    harden(ctx, plan, RootCauseRecord(statement="x", files=[]),
           candidates=[], issue=_Issue([]))
    assert [f.path for f in plan.files_to_change] == ["src/a.ts"]


def test_localization_candidates_also_count_as_support(tmp_path):
    """A caller the issue did not name, found by localization, is evidence
    too -- steering must not fight the signal it was built on."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "impl.ts").write_text("x\n")
    (tmp_path / "src" / "named.ts").write_text("y\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)

    plan = ChangePlan(files_to_change=[FileIntent("src/impl.ts")],
                      estimated_lines_changed=10)
    ctx = _Ctx(tmp_path)
    harden(ctx, plan, RootCauseRecord(statement="x", files=[]),
           candidates=["src/impl.ts"], issue=_Issue(["src/named.ts"]))
    assert [f.path for f in plan.files_to_change] == ["src/impl.ts"]
