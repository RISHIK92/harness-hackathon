"""GitHub ingestion: reference parsing, API fetch, clone, PR checkout.

Entirely offline: the REST calls are monkeypatched and the "remote" is a
local git repository.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harness import github as GH
from harness.logging_ui import Logger


def silent():
    return Logger(stream=io.StringIO())


# -- reference parsing -----------------------------------------------------
@pytest.mark.parametrize("text,owner,repo,number,is_pr", [
    ("owner/repo#123", "owner", "repo", 123, False),
    ("https://github.com/psf/requests/issues/6432", "psf", "requests", 6432,
     False),
    ("https://github.com/psf/requests/pull/6500", "psf", "requests", 6500,
     True),
    ("http://github.com/a/b/pull/1", "a", "b", 1, True),
    ("https://www.github.com/a/b/issues/9", "a", "b", 9, False),
    ("https://github.com/a/b.git/issues/9", "a", "b", 9, False),
])
def test_parse_ref(text, owner, repo, number, is_pr):
    ref = GH.parse_ref(text)
    assert ref is not None
    assert (ref.owner, ref.repo, ref.number, ref.is_pr) == (owner, repo,
                                                            number, is_pr)


@pytest.mark.parametrize("text", [
    "parse_date crashes with IndexError when the input has no separator",
    "see owner/repo#12 for context",          # prose, not a reference
    "",
    "just some words",
    "https://gitlab.com/a/b/issues/1",
])
def test_ordinary_issue_text_is_not_a_reference(text):
    assert GH.parse_ref(text) is None
    assert GH.looks_like_ref(text) is False


# -- fetching --------------------------------------------------------------
def fake_api(monkeypatch, issue: dict, comments: list, repo: dict,
             review: list | None = None):
    monkeypatch.setattr(GH, "_gh_available", lambda: False)

    def api(method, url, headers, body=None, timeout=15.0):
        if url.endswith("/comments") and "/pulls/" in url:
            return review or []
        if url.endswith("/comments"):
            return comments
        if "/issues/" in url or "/pulls/" in url:
            return issue
        return repo

    monkeypatch.setattr(GH, "request", api)


def test_issue_text_includes_body_and_comments(monkeypatch):
    fake_api(monkeypatch,
             issue={"title": "parse_date crashes", "body": "It raises.",
                    "labels": [{"name": "bug"}], "state": "open"},
             comments=[{"user": {"login": "ada"},
                        "body": "Traceback: IndexError at line 11"}],
             repo={"clone_url": "https://github.com/a/b.git",
                   "default_branch": "main"})
    out = GH.fetch(GH.parse_ref("a/b#1"), silent())
    text = out.as_issue_text()
    assert "parse_date crashes" in text
    assert "It raises." in text
    assert "labels: bug" in text
    assert "IndexError at line 11" in text, "comments carry the repro"
    assert "comment 1 by ada" in text


def test_pr_fetch_collects_review_comments(monkeypatch):
    fake_api(monkeypatch,
             issue={"title": "Fix the parser", "body": "Attempt one.",
                    "head": {"ref": "feature/parse"}},
             comments=[{"user": {"login": "bob"}, "body": "please fix"}],
             repo={"clone_url": "https://github.com/a/b.git",
                   "default_branch": "main"},
             review=[{"user": {"login": "cat"}, "path": "src/p.py",
                      "body": "this is off by one"}])
    out = GH.fetch(GH.parse_ref("https://github.com/a/b/pull/7"), silent())
    assert out.head_ref == "feature/parse"
    text = out.as_issue_text()
    assert "[on src/p.py] this is off by one" in text
    assert "please fix" in text


def test_missing_issue_gives_an_actionable_error(monkeypatch):
    from harness.model.types import ProviderError
    monkeypatch.setattr(GH, "_gh_available", lambda: False)

    def api(*a, **k):
        raise ProviderError("HTTP 404", status=404)

    monkeypatch.setattr(GH, "request", api)
    with pytest.raises(GH.GitHubError, match="GITHUB_TOKEN"):
        GH.fetch(GH.parse_ref("a/b#1"), silent())


def test_rate_limit_message_names_the_fix(monkeypatch):
    from harness.model.types import ProviderError
    monkeypatch.setattr(GH, "_gh_available", lambda: False)

    def api(*a, **k):
        raise ProviderError("HTTP 403", status=403)

    monkeypatch.setattr(GH, "request", api)
    with pytest.raises(GH.GitHubError, match="60 to 5000"):
        GH.fetch(GH.parse_ref("a/b#1"), silent())


def test_gh_cli_is_used_when_available(monkeypatch):
    monkeypatch.setattr(GH, "_gh_available", lambda: True)
    seen = {}

    def gh_json(args):
        seen["args"] = args
        return {"title": "from gh", "body": "b",
                "comments": [{"author": {"login": "z"}, "body": "c"}],
                "labels": [], "state": "open"}

    monkeypatch.setattr(GH, "_gh_json", gh_json)
    monkeypatch.setattr(GH, "_api", lambda p, timeout=15.0: {
        "clone_url": "https://github.com/a/b.git", "default_branch": "main"})
    out = GH.fetch(GH.parse_ref("a/b#3"), silent())
    assert out.title == "from gh"
    assert seen["args"][:2] == ["issue", "view"]
    assert "--repo" in seen["args"]


def test_falls_back_to_the_api_when_gh_fails(monkeypatch):
    """gh installed but not authenticated must not stop the run."""
    monkeypatch.setattr(GH, "_gh_available", lambda: True)
    monkeypatch.setattr(GH, "_gh_json", lambda args: None)
    fake_api(monkeypatch,
             issue={"title": "via api", "body": "b"},
             comments=[],
             repo={"clone_url": "https://github.com/a/b.git",
                   "default_branch": "main"})
    monkeypatch.setattr(GH, "_gh_available", lambda: True)
    out = GH.fetch(GH.parse_ref("a/b#4"), silent())
    assert out.title == "via api"


# -- token handling --------------------------------------------------------
def test_token_is_sent_when_present(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_FAKEtoken1234567890")
    assert GH._headers()["Authorization"].endswith("ghp_FAKEtoken1234567890")


def test_no_token_is_still_a_valid_request(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert "Authorization" not in GH._headers()


def test_private_clone_url_embeds_the_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_FAKEtoken1234567890")
    url = GH._authed_url("https://github.com/a/b.git")
    assert url.startswith("https://x-access-token:ghp_FAKEtoken")


def test_the_token_is_redacted_from_logs(monkeypatch):
    buf = io.StringIO()
    token = "ghp_FAKEtoken1234567890abcdef"
    log = Logger(secrets=[token], stream=buf)
    log.line(f"cloning https://x-access-token:{token}@github.com/a/b.git")
    assert token not in buf.getvalue()


# -- cloning ---------------------------------------------------------------
def make_remote(tmp_path: Path) -> Path:
    """A local git repository standing in for a GitHub remote."""
    remote = tmp_path / "remote"
    remote.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
           "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)}
    (remote / "mod.py").write_text("def f():\n    return 1\n")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=remote, check=True,
                       capture_output=True, env=env)
    return remote


def test_clone_creates_a_working_branch(tmp_path):
    remote = make_remote(tmp_path)
    fetched = GH.Fetched(ref=GH.parse_ref("a/b#42"), title="t", body="",
                         clone_url=str(remote), default_branch="main")
    target = GH.clone(fetched, tmp_path / "ws", silent())
    assert (target / ".git").is_dir()
    assert (target / "mod.py").is_file()
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=target,
                            capture_output=True, text=True).stdout.strip()
    assert branch == "harness/issue-42", "work must not land on the default branch"


def test_clone_is_reused_when_it_already_exists(tmp_path):
    remote = make_remote(tmp_path)
    fetched = GH.Fetched(ref=GH.parse_ref("a/b#42"), title="t", body="",
                         clone_url=str(remote))
    first = GH.clone(fetched, tmp_path / "ws", silent())
    (first / "scratch.txt").write_text("keep me")
    second = GH.clone(fetched, tmp_path / "ws", silent())
    assert first == second
    assert (second / "scratch.txt").is_file()


def test_clone_failure_is_actionable(tmp_path):
    fetched = GH.Fetched(ref=GH.parse_ref("a/b#1"), title="t", body="",
                         clone_url=str(tmp_path / "does-not-exist"))
    with pytest.raises(GH.GitHubError, match="clone failed"):
        GH.clone(fetched, tmp_path / "ws", silent())


def test_prepare_returns_none_for_ordinary_text():
    assert GH.prepare("parse_date crashes", None, silent()) is None


# -- end to end: a reference in, a verified fix out -------------------------
def test_a_github_reference_drives_a_complete_run(tmp_path, monkeypatch):
    """The whole point: hand it `owner/repo#N` and nothing else."""
    sys.path.insert(0, str(ROOT / "tests"))
    import mock_model
    from harness import exits
    from harness.__main__ import main

    # a local repository standing in for the GitHub remote
    fixture = ROOT / "tests" / "fixtures" / "py-offbyone"
    if not fixture.is_dir():
        pytest.skip("fixtures not generated")
    remote = tmp_path / "remote"
    subprocess.run(["git", "clone", "-q", str(fixture), str(remote)],
                   check=True, capture_output=True)
    meta = json.loads((fixture / ".fixture.json").read_text())

    mock_model.install(monkeypatch)
    monkeypatch.setattr(GH, "_gh_available", lambda: False)

    def api(method, url, headers, body=None, timeout=15.0):
        if url.endswith("/comments"):
            return [{"user": {"login": "ada"},
                     "body": "Traceback ends in parse_date."}]
        if "/issues/" in url:
            return {"title": "parse_date crashes on input with no separator",
                    "body": meta["issue"], "labels": [{"name": "bug"}],
                    "state": "open"}
        return {"clone_url": str(remote), "default_branch": "main"}

    monkeypatch.setattr(GH, "request", api)
    monkeypatch.setenv("AI_API_KEY", "sk-ant-api03-FAKEGH1234567890")
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("HARNESS_NO_CACHE", "1")
    monkeypatch.setenv("ISSUE", "acme/dateparse#77")
    for k in ("REPO_PATH", "HARNESS_DRY_RUN", "HARNESS_TASK_TYPE",
              "HARNESS_ROUTE", "ISSUE_FILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)

    rc = main(["harness"])
    assert rc == exits.SUCCESS

    clone_dir = tmp_path / "ws" / "dateparse-77"
    assert clone_dir.is_dir(), "the harness must clone it itself"

    branch = subprocess.run(["git", "branch", "--show-current"],
                            cwd=clone_dir, capture_output=True,
                            text=True).stdout.strip()
    assert branch == "harness/issue-77"

    changed = subprocess.run(["git", "diff", "--name-only"], cwd=clone_dir,
                             capture_output=True, text=True).stdout.split()
    assert changed == ["src/dateparse/parser.py"]

    # the fix must actually work in the clone
    pyexe = str((ROOT / ".venv" / "bin" / "python").resolve())
    tests = subprocess.run([pyexe, "-m", "pytest", "-q"], cwd=clone_dir,
                           capture_output=True, text=True)
    assert tests.returncode == 0, tests.stdout[-600:]

    report = clone_dir / ".harness" / "run" / "run_report.md"
    assert report.is_file()
    assert "parse_date" in report.read_text()


def test_a_pull_request_reference_checks_out_the_pr_branch(tmp_path,
                                                           monkeypatch):
    fixture = ROOT / "tests" / "fixtures" / "py-offbyone"
    if not fixture.is_dir():
        pytest.skip("fixtures not generated")
    remote = tmp_path / "remote"
    subprocess.run(["git", "clone", "-q", str(fixture), str(remote)],
                   check=True, capture_output=True)
    # a PR ref, as GitHub exposes it
    subprocess.run(["git", "update-ref", "refs/pull/5/head", "HEAD"],
                   cwd=remote, check=True, capture_output=True)

    fetched = GH.Fetched(ref=GH.parse_ref("https://github.com/a/b/pull/5"),
                         title="t", body="", clone_url=str(remote),
                         default_branch="main", head_ref="feature/x")
    target = GH.clone(fetched, tmp_path / "ws", silent())
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=target,
                            capture_output=True, text=True).stdout.strip()
    assert branch == "pr-5", "a PR must be worked on its own branch"


# -- posting back ----------------------------------------------------------
def test_posting_is_off_by_default(monkeypatch):
    from harness import publish
    monkeypatch.delenv("HARNESS_POST", raising=False)
    assert publish.enabled() is False


def test_posting_never_fires_on_a_failed_run(monkeypatch):
    from harness import exits, publish
    monkeypatch.setenv("HARNESS_POST", "comment")
    called = []
    monkeypatch.setattr(publish, "post_comment",
                        lambda *a, **k: called.append(1))

    class S:
        cycles = 5
    publish.publish(None, silent(), GH.parse_ref("a/b#1"), exits.NO_FIX,
                    S(), "report")
    assert not called, "a run with no verified fix must not comment"

    publish.publish(None, silent(), GH.parse_ref("a/b#1"), exits.SUCCESS,
                    S(), "report")
    assert called == [1]


def test_no_reference_means_nothing_is_posted(monkeypatch):
    from harness import exits, publish
    monkeypatch.setenv("HARNESS_POST", "comment")
    called = []
    monkeypatch.setattr(publish, "post_comment",
                        lambda *a, **k: called.append(1))

    class S:
        cycles = 1
    publish.publish(None, silent(), None, exits.SUCCESS, S(), "r")
    assert not called


def test_the_comment_says_nothing_was_pushed(monkeypatch):
    from harness import exits, publish

    class S:
        cycles = 2
    body = publish._preamble(exits.SUCCESS, S())
    assert "nothing has been pushed" in body
    assert "resolved" in body
