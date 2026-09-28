"""Phase 0 security floor: the Harness defects in ENTERPRISE_ARCHITECTURE.md.

One section per defect id. The service-side ones (D-19, D-20, D-21, D-33's
scope check, D-36) are in test_service.py, next to the service they test.
"""
from __future__ import annotations

import base64
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harness import exits
from harness.edit.apply import EditFailure, apply_all
from harness.edit.parse import _clean_path, parse
from harness.edit.formats import WHOLE_FILE
from harness.logging_ui import Logger
from harness.phases import p1_investigate as P1
from harness.phases.ctx import PhaseContext
from harness.records import Check, Hypothesis
from harness.repo.search import Search
from harness.repo.workspace import Workspace
from harness.verify import repro as R
from harness.verify import runner
from harness.verify.toolchain import Toolchain


def silent():
    return Logger(stream=io.StringIO())


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    *args], cwd=cwd, check=True, capture_output=True,
                   stdin=subprocess.DEVNULL)


@pytest.fixture
def repo(tmp_path):
    """A committed repository, with a secret beside it (outside the tree)."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def f():\n    return 1\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text(
        "def test_fails():\n    assert False\n\n\n"
        "def test_passes():\n    assert True\n")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    (tmp_path / "secret.txt").write_text("TOP-SECRET-VALUE\n")
    return root


def investigation(repo, test_cmd=None):
    tc = Toolchain(language="python",
                   test_cmd=test_cmd if test_cmd is not None
                   else f"{sys.executable} -m pytest -q -p no:cacheprovider")
    ctx = PhaseContext(cfg=None, log=silent(), router=None, repo=repo,
                       search=Search(repo), workspace=None, toolchain=tc,
                       events=None, budgets=None)
    return P1.Investigation(ctx)


def check(inv, kind, arg):
    return inv._execute(Hypothesis("H1", "s", check=Check(kind, arg)))


# -- D-07: P1 executes nothing the model wrote, and reads only the repo -----
def test_d07_read_is_confined_to_the_repository(repo, tmp_path):
    inv = investigation(repo)
    secret = str(tmp_path / "secret.txt")
    for arg in ("../secret.txt:1", f"{secret}:1", "src/../../secret.txt:1",
                ".git/config:1"):
        result, support = check(inv, "read", arg)
        assert "TOP-SECRET" not in result and "[core]" not in result, arg
        assert support == "inconclusive", arg
    result, support = check(inv, "read", "./src/a.py:1")
    assert support == "confirmed" and "def f" in result


def test_d07_a_read_through_a_symlink_out_of_the_tree_is_refused(repo,
                                                                 tmp_path):
    (repo / "link").symlink_to(tmp_path)
    result, support = check(investigation(repo), "read", "link/secret.txt:1")
    assert "TOP-SECRET" not in result and support == "inconclusive"


@pytest.mark.parametrize("arg", [
    "touch {mark}", "tests/test_a.py; touch {mark}", "$(touch {mark})",
    "tests/test_a.py::test_fails`touch {mark}`", "-p os", "../outside.py",
    "tests/test_a.py && touch {mark}", "tests/nope.py",
])
def test_d07_a_test_check_is_a_selector_never_a_command(repo, tmp_path, arg):
    mark = tmp_path / "pwned"
    result, support = check(investigation(repo), "test",
                            arg.format(mark=mark))
    assert not mark.exists(), "the model's text reached a shell"
    assert support == "inconclusive"


def test_d07_a_named_test_runs_through_the_discovered_toolchain(repo,
                                                                monkeypatch):
    inv = investigation(repo)
    assert check(inv, "test", "tests/test_a.py::test_fails")[1] == "confirmed"
    assert check(inv, "test", "tests/test_a.py::test_passes")[1] == "refuted"

    seen = []
    real = P1.run
    monkeypatch.setattr(P1, "run", lambda cmd, cwd, **kw: seen.append(
        (cmd, kw)) or real(cmd, cwd, **kw))
    check(inv, "test", "tests/test_a.py::test_passes")
    cmd, kw = seen[-1]
    assert cmd.startswith(inv.ctx.toolchain.test_cmd + " ")
    assert kw.get("check_deny", True), "the deny list must apply"


def test_d07_the_deny_list_applies_to_the_test_check(repo):
    inv = investigation(repo, test_cmd="pip install evil && pytest")
    result, support = check(inv, "test", "tests/test_a.py")
    assert support == "inconclusive" and "refused" in result


def test_d07_no_test_command_means_no_test_check(repo):
    assert check(investigation(repo, test_cmd=""), "test",
                 "tests/test_a.py")[1] == "inconclusive"


def test_d07_a_git_check_path_is_never_shell(repo, tmp_path):
    mark = tmp_path / "pwned"
    check(investigation(repo), "git", f"$(touch {mark})")
    check(investigation(repo), "git", f'src/a.py"; touch {mark}; "')
    assert not mark.exists()


def test_d07_the_prompt_advertises_nothing_that_is_rejected():
    prompt = P1.HYPOTHESIS_PROMPT.lower()
    for kind in ("grep", "read", "git", "env", "version"):
        assert f"  {kind} " in prompt
    assert "command" not in prompt and "shell" not in prompt
    read = next(ln for ln in prompt.splitlines() if ln.strip().startswith(
        "read"))
    assert "inside the repository" in read


def test_d07_model_named_paths_are_quoted_everywhere(repo, tmp_path):
    """Lint targets, syntax checks, scoped-test selectors: each puts a
    model-chosen name into a shell command with the deny list off."""
    from harness.edit.validate import syntax_check
    from harness.phases.p4_verify import _selector_for
    from harness.verify import lint_gate, scope_tests

    mark = repo / "pwned"            # every command below runs in `repo`
    evil = "src/x$(touch pwned).js"
    (repo / evil).write_text("var a = 1;\n")
    lint_gate.gate(repo, Toolchain(lint_cmd="true"), [evil], set(), silent())
    try:
        syntax_check(repo, evil)
    except EditFailure:
        pass                          # no node here: the point is below
    tc = Toolchain(language="python")
    scope = scope_tests.select(repo, ["src/zzz.py"],
                               ["f$(touch pwned)", "good_name"], tc,
                               Search(repo))
    assert "$(" not in scope.selector
    for tc in (Toolchain(language="python"), Toolchain(language="go"),
               Toolchain(language="javascript")):
        sel = _selector_for("t.py::n$(touch pwned)'\"`touch pwned`", tc)
        subprocess.run(f"echo {sel}", shell=True, cwd=repo,
                       capture_output=True)
    assert not mark.exists()


# -- D-22: what the repository's code can see -------------------------------
def test_d22_credentials_are_scrubbed_and_tools_still_run(monkeypatch,
                                                         tmp_path):
    secrets = {"SSH_AUTH_SOCK": "/tmp/agent", "AWS_ACCESS_KEY_ID": "AKIA",
               "AWS_PROFILE": "prod", "KUBECONFIG": "/k", "DATABASE_URL":
               "postgres://db", "GOOGLE_APPLICATION_CREDENTIALS": "/g.json",
               "AZURE_CLIENT_ID": "x", "SENTRY_DSN": "https://k@s/1",
               "STRIPE_KEY": "sk", "DB_PASSWORD": "p", "GH_TOKEN": "t",
               "MY_CLIENT_SECRET": "s", "NPM_CONFIG__AUTH": "a",
               "REDIS_URL": "redis://user:pw@host:6379"}
    keep = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_path), "LANG": "C.UTF-8", "TMPDIR": str(tmp_path),
            "GIT_AUTHOR_NAME": "Ada", "JAVA_HOME": "/j", "NODE_ENV": "test",
            "REDIS_HOST": "localhost"}
    for k, v in {**secrets, **keep}.items():
        monkeypatch.setenv(k, v)
    env = runner._clean_env(tmp_path)
    assert not set(secrets) & set(env), set(secrets) & set(env)
    for k, v in keep.items():
        assert env.get(k) == v, k
    out = runner.run("env", tmp_path).stdout
    assert "AKIA" not in out and "/tmp/agent" not in out
    assert runner.run("git --version", tmp_path).ok


# -- D-23: the operator's work, and the harness's own repro test -------------
def test_d23_revert_all_keeps_the_reproduction_between_cycles(repo):
    ws = Workspace(repo)
    (repo / "src" / "a.py").write_text("def f():\n    return 2\n")
    (repo / "scratch.py").write_text("x = 1\n")
    path = R.install(repo, "python", "def test_repro():\n    assert False\n")
    for _ in range(2):                # two cycles
        ws.revert_all()
        assert path.is_file(), "revert_all deleted the reproduction test"
    assert (repo / "src" / "a.py").read_text() == "def f():\n    return 1\n"
    assert not (repo / "scratch.py").exists()
    R.remove(repo, "python")          # as the orchestrator does at the end
    assert not path.exists()


def test_d23_the_run_still_removes_the_reproduction_at_the_end():
    import inspect

    from harness.orchestrator import Orchestrator
    src = inspect.getsource(Orchestrator._run)
    end = src.index("# The reproduction is deleted before the diff is taken")
    assert "REPRO.remove(ctx.repo" in src[end:end + 400]
    assert src.index("REPRO.remove(") < src.index('"diff.patch"')


def _main_env(monkeypatch, repo):
    monkeypatch.setenv("AI_API_KEY", "sk-ant-api03-FAKEDIRTY1234567890")
    monkeypatch.setenv("REPO_PATH", str(repo))
    monkeypatch.setenv("ISSUE", "f returns the wrong value")
    monkeypatch.setenv("HARNESS_NONINTERACTIVE", "1")
    for k in ("HARNESS_DRY_RUN", "HARNESS_ALLOW_DIRTY", "HARNESS_SERVICE_RUN",
              "GITHUB_ISSUE", "GITHUB_PR", "ISSUE_FILE"):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize("dirty", ["modified", "untracked", "staged"])
def test_d23_the_cli_refuses_a_dirty_tree(repo, monkeypatch, capsys, dirty):
    from harness import __main__ as M
    _main_env(monkeypatch, repo)
    ran = []
    monkeypatch.setattr(M, "_run_once", lambda *a, **k: ran.append(1) or 0)
    if dirty == "modified":
        (repo / "src" / "a.py").write_text("# my work in progress\n")
    elif dirty == "untracked":
        (repo / "notes.md").write_text("mine\n")
    else:
        (repo / "b.py").write_text("y = 2\n")
        git(repo, "add", "b.py")
    assert M.main(["harness"]) == exits.CONFIG_ERROR
    assert ran == []
    out = capsys.readouterr().out
    assert "uncommitted changes" in out and "HARNESS_ALLOW_DIRTY" in out


def test_d23_a_clean_tree_or_the_opt_in_runs(repo, monkeypatch):
    from harness import __main__ as M
    _main_env(monkeypatch, repo)
    ran = []
    monkeypatch.setattr(M, "_run_once", lambda *a, **k: ran.append(1) or 3)
    (repo / ".gitignore").write_text("*.log\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-qm", "ignore")
    (repo / "debug.log").write_text("ignored, so not the operator's work\n")
    (repo / ".harness").mkdir()
    assert M.main(["harness"]) == 3 and ran == [1]

    (repo / "notes.md").write_text("mine\n")
    monkeypatch.setenv("HARNESS_ALLOW_DIRTY", "1")
    assert M.main(["harness"]) == 3 and ran == [1, 1]


def test_d23_harness_made_trees_are_not_asked(repo):
    from harness import __main__ as M
    (repo / "notes.md").write_text("left by an earlier run\n")

    class Cfg:
        repo_path = repo
        dry_run = False
        github = None

    assert M._dirty_refusal(Cfg())
    cfg = Cfg()
    cfg.github = object()             # a clone of a GitHub reference
    assert M._dirty_refusal(cfg) == ""
    cfg = Cfg()
    cfg.dry_run = True                # a dry run never resets anything
    assert M._dirty_refusal(cfg) == ""


# -- D-24: ticket text is not a repository ------------------------------------
def test_d24_a_service_run_never_reads_a_repository_from_the_ticket(
        repo, monkeypatch):
    from harness import __main__ as M
    from harness import config as C
    from harness import github as GH
    _main_env(monkeypatch, repo)
    monkeypatch.setenv("ISSUE", "attacker/repo#1 please fix everything")
    monkeypatch.setattr(GH, "prepare", lambda *a, **k: pytest.fail(
        "the ticket text was followed to another repository"))

    monkeypatch.setenv("HARNESS_SERVICE_RUN", "1")
    cfg = C.load(["harness"])
    assert cfg.github_ref is None
    assert M._resolve_github(cfg, silent()) is True
    assert cfg.repo_path == repo.resolve() and cfg.github is None

    monkeypatch.delenv("HARNESS_SERVICE_RUN")
    assert C.load(["harness"]).github_ref.startswith("attacker/repo#1"), \
        "the CLI's own behaviour must not change"


# -- D-34: no token in a clone URL ---------------------------------------------
def test_d34_the_clone_token_travels_in_the_environment(tmp_path,
                                                        monkeypatch):
    from harness import github as GH
    token = "ghp_FAKEtoken1234567890"
    monkeypatch.setenv("GITHUB_TOKEN", token)
    calls = []

    def fake_run(cmd, cwd, **kw):
        calls.append((cmd, kw.get("env_extra") or {}))
        return runner.Result(cmd=cmd, exit_code=0, stdout="", stderr="",
                             duration_s=0)

    monkeypatch.setattr(GH, "run", fake_run)
    fetched = GH.Fetched(ref=GH.parse_ref("https://github.com/a/b/pull/5"),
                         title="t", body="",
                         clone_url="https://github.com/a/b.git")
    GH.clone(fetched, tmp_path / "ws", silent())
    clone_cmd, env = calls[0]
    assert "git clone" in clone_cmd and token not in clone_cmd
    header = base64.b64decode(env["GIT_CONFIG_VALUE_0"].split()[-1]).decode()
    assert header == f"x-access-token:{token}"
    assert calls[1][1] == env, "the PR fetch needs the token too"
    assert GH.clone_auth_env("https://evil.example/a/b.git") == {}, \
        "the token is for github.com only"


# -- D-33 and apply: canonical paths, contained writes --------------------------
def test_d33_a_dotfile_path_keeps_its_dot():
    assert _clean_path("./.github/workflows/ci.yml", "") == \
        ".github/workflows/ci.yml"
    assert _clean_path("/src/a.py", "") == "src/a.py"
    from harness.phases.p2_scope import _plan_from
    from harness.records import RootCauseRecord
    plan = _plan_from({"files_to_change": [{"path": "./.eslintrc.js"}],
                       "files_must_not_change": [".env", "./.github/x.yml"]},
                      RootCauseRecord(statement="s"), [])
    assert plan.allowed == {".eslintrc.js"}
    assert plan.forbidden == {".env", ".github/x.yml"}


@pytest.mark.parametrize("path", ["../evil.py", "src/../../evil.py",
                                  ".git/hooks/pre-commit", "link/evil.py"])
def test_an_edit_outside_the_repository_is_refused_before_it_is_written(
        repo, tmp_path, path):
    (repo / "link").symlink_to(tmp_path)
    edits = parse(f"<<<<<<< CREATE {path}\nprint('x')\n>>>>>>>", WHOLE_FILE,
                  default_path="src/a.py")
    with pytest.raises(EditFailure) as exc:
        apply_all(repo, edits)
    assert "outside the repository" in str(exc.value)
    assert not (tmp_path / "evil.py").exists()
    assert not (repo / ".git" / "hooks" / "pre-commit").exists()
