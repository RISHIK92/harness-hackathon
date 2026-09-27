"""The @ repository finder: bounded scan, sane ranking."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harness import finder


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir(exist_ok=True)
    (path / "README.md").write_text("x")
    return path


def test_finds_repositories_and_does_not_descend_into_them(tmp_path,
                                                           monkeypatch):
    git_repo(tmp_path / "alpha")
    git_repo(tmp_path / "alpha" / "nested")     # must not be reported
    git_repo(tmp_path / "work" / "beta")
    (tmp_path / "plain").mkdir()
    monkeypatch.setattr(finder, "roots", lambda: [tmp_path])
    names = {r.name for r in finder.discover()}
    assert names == {"alpha", "beta"}


def test_skips_dependency_and_build_directories(tmp_path, monkeypatch):
    git_repo(tmp_path / "node_modules" / "pkg")
    git_repo(tmp_path / ".venv" / "lib")
    git_repo(tmp_path / "real")
    monkeypatch.setattr(finder, "roots", lambda: [tmp_path])
    assert {r.name for r in finder.discover()} == {"real"}


def test_the_scan_is_bounded_in_depth(tmp_path, monkeypatch):
    deep = tmp_path.joinpath(*[f"d{i}" for i in range(8)])
    git_repo(deep)
    git_repo(tmp_path / "shallow")
    monkeypatch.setattr(finder, "roots", lambda: [tmp_path])
    names = {r.name for r in finder.discover()}
    assert "shallow" in names
    assert deep.name not in names, "an unbounded walk takes minutes"


def test_the_scan_is_bounded_in_time(tmp_path, monkeypatch):
    for i in range(40):
        git_repo(tmp_path / f"r{i}")
    monkeypatch.setattr(finder, "roots", lambda: [tmp_path])
    started = time.time()
    finder.discover()
    assert time.time() - started < finder.TIME_BUDGET + 2


def test_results_are_cached(tmp_path, monkeypatch):
    git_repo(tmp_path / "one")
    monkeypatch.setattr(finder, "roots", lambda: [tmp_path])
    cache = tmp_path / "cache"
    first = finder.discover(cache_dir=cache)
    git_repo(tmp_path / "two")
    second = finder.discover(cache_dir=cache)
    assert [r.name for r in second] == [r.name for r in first]
    assert len(finder.discover(cache_dir=cache, refresh=True)) == 2


# -- ranking ---------------------------------------------------------------
def repos(*names):
    return [finder.Repo(path=f"/x/{n}", name=n) for n in names]


def test_a_prefix_beats_a_substring():
    out = finder.match(repos("my-harness", "harness-hackathon"), "harn")
    assert out[0].name == "harness-hackathon"


def test_a_loose_subsequence_does_not_match():
    """"harn" is a subsequence of "Asynchronous-File-Concatenator", which
    made the picker useless."""
    out = finder.match(
        repos("Asynchronous-File-Concatenator-Node-Handling", "harness"),
        "harn")
    assert [r.name for r in out] == ["harness"]


def test_a_tight_subsequence_does_match():
    out = finder.match(repos("harness-hackathon", "unrelated"), "hh")
    assert out and out[0].name == "harness-hackathon"


def test_a_path_match_ranks_below_a_name_match():
    a = finder.Repo(path="/home/webdev/alpha", name="alpha")
    b = finder.Repo(path="/home/x/webdev-tools", name="webdev-tools")
    out = finder.match([a, b], "webdev")
    assert out[0].name == "webdev-tools"


def test_no_match_returns_nothing():
    assert finder.match(repos("alpha", "beta"), "zzzz") == []


def test_an_empty_query_returns_everything():
    all_repos = repos("a", "b")
    assert finder.match(all_repos, "") == all_repos


def test_home_is_collapsed_for_display():
    home = str(Path.home())
    r = finder.Repo(path=f"{home}/code/thing", name="thing")
    assert r.home == "~/code/thing"
