"""Git history as investigation evidence (FR-16, SPEC.md 4.6).

A bug that "just appeared" is almost always a recent commit, which makes this
the highest-signal cheap step in investigation.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path

from ..verify.runner import run


@dataclass
class Commit:
    sha: str
    date: str
    subject: str

    def render(self) -> str:
        return f"{self.sha} {self.date} {self.subject}"


@dataclass
class FileHistory:
    path: str
    commits: list = field(default_factory=list)
    hunks: str = ""
    recent: bool = False

    def render(self, limit: int = 5) -> str:
        if not self.commits:
            return f"{self.path}: no history"
        head = f"{self.path}: {len(self.commits)} commits"
        if self.recent:
            head += "  [changed recently]"
        body = "\n".join("  " + c.render() for c in self.commits[:limit])
        return head + "\n" + body


def _git(repo: Path, args: str, timeout: float = 30.0):
    # Harness-built git queries, so no deny list. The one variable part is a
    # path -- and the path can be the model's (a P1 `git` check) or the
    # issue's -- so it is always passed through `_q`. It used to sit inside
    # double quotes, where `$(...)` is still a command.
    return run(f"git {args}", repo, timeout=timeout, check_deny=False)


def _q(path: str) -> str:
    return shlex.quote(str(path))


def file_history(repo: Path, path: str, n: int = 10,
                 recent_days: int = 90) -> FileHistory:
    fh = FileHistory(path=path)
    r = _git(repo, f'log -n {n} --format="%h|%ad|%s" --date=short -- {_q(path)}')
    if not r.ok:
        return fh
    for line in r.stdout.splitlines():
        line = line.strip().strip('"')
        parts = line.split("|", 2)
        if len(parts) == 3:
            fh.commits.append(Commit(*parts))

    rec = _git(repo, f'log --since={recent_days}.days --oneline -- {_q(path)}')
    fh.recent = bool(rec.ok and rec.stdout.strip())

    patch = _git(repo, f'log -p -n 3 --format="%h %s" -- {_q(path)}')
    if patch.ok:
        fh.hunks = _trim_patch(patch.stdout)
    return fh


def _trim_patch(text: str, context: int = 3) -> str:
    """Hunk headers plus a few lines, never whole diffs."""
    out, keep = [], 0
    for line in text.splitlines():
        if line.startswith("@@"):
            out.append(line)
            keep = context * 2
        elif line.startswith(("+++", "---", "diff --git")):
            continue
        elif keep > 0:
            out.append(line[:160])
            keep -= 1
        elif line and not line.startswith(("+", "-", " ")):
            out.append(line[:120])
    return "\n".join(out[:120])


def blame(repo: Path, path: str, start: int, end: int) -> str:
    r = _git(repo, f'blame -L {start},{end} --date=short -- {_q(path)}')
    return r.stdout[:3000] if r.ok else ""


def manifest_changes(repo: Path, days: int = 90) -> list[Commit]:
    """Recent changes to dependency manifests -- a breaking-upgrade signal."""
    manifests = ("pyproject.toml requirements.txt requirements.lock "
                 "poetry.lock package.json package-lock.json yarn.lock "
                 "go.mod go.sum Cargo.toml Cargo.lock Gemfile.lock "
                 "composer.json composer.lock")
    r = _git(repo, f'log --since={days}.days --format="%h|%ad|%s" '
                   f'--date=short -- {manifests}')
    out = []
    if r.ok:
        for line in r.stdout.splitlines():
            parts = line.strip().strip('"').split("|", 2)
            if len(parts) == 3:
                out.append(Commit(*parts))
    return out


def co_changed(repo: Path, path: str, days: int = 730,
               top: int = 5) -> list[tuple[str, int]]:
    """Files that historically change together with `path` (SPEC.md 21.4)."""
    r = _git(repo, f'log --since={days}.days --format=%H --name-only -- {_q(path)}')
    if not r.ok:
        return []
    counts: dict[str, int] = {}
    current: list[str] = []
    for line in r.stdout.splitlines() + [""]:
        line = line.strip()
        if not line:
            for f in current:
                if f != path:
                    counts[f] = counts.get(f, 0) + 1
            current = []
        elif len(line) == 40 and all(c in "0123456789abcdef" for c in line):
            continue
        else:
            current.append(line)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:top]
