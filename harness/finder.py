"""Find local git repositories, for the `@` picker (SPEC.md 11.1).

Bounded on purpose: a naive walk of a home directory takes minutes and finds
tens of thousands of directories. This scans a few likely roots to a shallow
depth, stops at a time budget, and caches what it found.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

# Home holds hundreds of unrelated directories, so it is scanned shallowly;
# the conventional code roots are worth going deeper into.
HOME_DEPTH = 2
DEEP_DEPTH = 4
TIME_BUDGET = 3.0
LIMIT = 300
CACHE_TTL = 900          # seconds

SKIP = {
    "node_modules", ".venv", "venv", "env", "site-packages", "__pycache__",
    ".git", ".tox", ".mypy_cache", ".pytest_cache", "dist", "build", "target",
    "Library", "Applications", "System", ".Trash", ".cache", ".npm", ".cargo",
    ".rustup", "go", ".gradle", ".m2", "vendor", ".terraform", "Pods",
    ".next", ".nuxt", "coverage", ".harness",
}

ROOTS = ("", "code", "src", "projects", "work", "dev", "repos", "git",
         "Developer", "Documents", "Desktop", "workspace")


@dataclass
class Repo:
    path: str
    name: str
    mtime: float = 0.0
    branch: str = ""

    @property
    def home(self) -> str:
        """The path with $HOME collapsed, which is what people recognise."""
        h = str(Path.home())
        return "~" + self.path[len(h):] if self.path.startswith(h) else self.path


def roots() -> list:
    """Most likely first, so a budget overrun still returns the good ones."""
    home = Path.home()
    out = [Path.cwd()]
    for name in ROOTS:
        if not name:
            continue                     # home comes last: it is the slowest
        candidate = home / name
        if candidate.is_dir() and candidate not in out:
            out.append(candidate)
    if home not in out:
        out.append(home)
    return out


def _scan(root: Path, found: dict, deadline: float, depth: int = 0,
          max_depth: int = DEEP_DEPTH) -> None:
    if time.time() > deadline or depth > max_depth or len(found) >= LIMIT:
        return
    try:
        entries = list(os.scandir(root))
    except (OSError, PermissionError):
        return
    if any(e.name == ".git" for e in entries):
        try:
            # The .git directory's mtime tracks commits and checkouts, and
            # costs one stat rather than one per entry.
            mtime = (root / ".git").stat().st_mtime
        except (OSError, PermissionError):
            mtime = 0.0
        found[str(root)] = Repo(str(root), root.name, mtime)
        return               # do not descend into a repository
    for entry in entries:
        if time.time() > deadline:
            return
        if not entry.is_dir(follow_symlinks=False):
            continue
        if entry.name in SKIP or entry.name.startswith("."):
            continue
        _scan(Path(entry.path), found, deadline, depth + 1, max_depth)


def discover(cache_dir: Path | None = None, refresh: bool = False) -> list:
    """Repositories, most recently touched first."""
    cache = Path(cache_dir) / "repos.json" if cache_dir else None
    if cache and not refresh and cache.is_file():
        try:
            age = time.time() - cache.stat().st_mtime
            if age < CACHE_TTL:
                data = json.loads(cache.read_text("utf-8"))
                return [Repo(**r) for r in data]
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    found: dict = {}
    deadline = time.time() + TIME_BUDGET
    home = Path.home()
    for root in roots():
        depth = HOME_DEPTH if root == home else DEEP_DEPTH
        _scan(root, found, deadline, max_depth=depth)
    repos = sorted(found.values(), key=lambda r: (-r.mtime, r.name))

    if cache:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps([r.__dict__ for r in repos]),
                             encoding="utf-8")
        except OSError:
            pass
    return repos


def match(repos: list, query: str) -> list:
    """Rank by how directly the query names the repository.

    A plain subsequence test is far too loose: "harn" is a subsequence of
    "Asynchronous-File-Concatenator", so it would rank alongside the repo
    actually called harness. A subsequence only counts when it is tight.
    """
    q = (query or "").strip().lower()
    if not q:
        return repos
    scored = []
    for r in repos:
        name, path = r.name.lower(), r.path.lower()
        if name.startswith(q):
            rank = (0, 0)
        elif q in name:
            rank = (1, name.index(q))
        elif _initials(name).startswith(q):
            rank = (2, 0)                 # "hh" -> harness-hackathon
        elif _tight_subsequence(q, name):
            rank = (3, 0)
        elif q in path:
            rank = (4, path.index(q))
        else:
            continue
        scored.append((rank[0], rank[1], -r.mtime, r.name, r))
    scored.sort(key=lambda item: item[:4])
    return [item[4] for item in scored]


def _initials(name: str) -> str:
    """First letters of the hyphen/underscore separated words.

    Typing "hh" for harness-hackathon is initials, not a subsequence, and
    treating it as one is what people actually mean."""
    import re as _re
    return "".join(part[0] for part in _re.split(r"[^A-Za-z0-9]+", name)
                   if part)


def _tight_subsequence(needle: str, haystack: str, slack: int = 3) -> bool:
    """The characters appear in order and close together: the whole match
    must fit inside `slack` times the needle. "hhak" reaches
    "harness-hackathon"; "harn" does not reach across
    "Asynchronous-File-Concatenator". Initials are tier 3's job, not this."""
    i = 0
    start = None
    for j, ch in enumerate(haystack):
        if ch == needle[i]:
            if start is None:
                start = j
            i += 1
            if i == len(needle):
                return (j - start + 1) <= max(len(needle) * slack, 8)
    return False
