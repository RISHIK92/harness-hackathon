"""Search with a three-deep fallback (SPEC.md 4.4).

rg -> git grep -> a Python walker.  None is a hard dependency, and results
are always capped: never whole files, always file:line plus one line of
context.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..verify.runner import run

MAX_HITS = 80
# Constructs POSIX ERE (what `git grep -E` speaks) does not support. git grep
# silently finds nothing for these rather than erroring, so a pattern using
# them must either go to `git grep -P` or fall through to the Python backend.
PCRE_ONLY = re.compile(r"\\[bBdDsSwWAZzh]|\(\?[:=!<]|\{\d")
SKIP_DIRS = {".git", ".harness", ".venv", "venv", "node_modules", "__pycache__",
             "dist", "build", ".tox", ".mypy_cache", ".pytest_cache", "target",
             ".worktrees", "vendor", ".idea", ".gradle"}
SKIP_SUFFIX = {".pyc", ".so", ".dylib", ".dll", ".png", ".jpg", ".jpeg", ".gif",
               ".pdf", ".zip", ".gz", ".tar", ".whl", ".ico", ".woff", ".woff2",
               ".lock", ".min.js", ".map", ".bin", ".class", ".jar"}
MAX_FILE_BYTES = 1_000_000

# Extensions that can contain a call site. Data and config files mention
# identifiers too, and counting them as callers is a visible error.
SOURCE_SUFFIX = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
                 ".go", ".rs", ".java", ".kt", ".rb", ".php", ".c", ".h",
                 ".cpp", ".cc", ".hpp", ".cs", ".swift", ".scala", ".sh"}


def is_source(path: str) -> bool:
    from pathlib import Path as _P
    return _P(path).suffix.lower() in SOURCE_SUFFIX


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    text: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.text.strip()[:200]}"


class Search:
    def __init__(self, repo: Path, log=None) -> None:
        self.repo = Path(repo)
        self.log = log
        self._pcre: bool | None = None
        self.backend = ("rg" if shutil.which("rg") else
                        "git-grep" if (self.repo / ".git").exists() else
                        "python")
        if self.backend != "rg" and log:
            log.degraded("slow_search", f"using {self.backend}")

    def grep(self, pattern: str, globs: list[str] | None = None,
             max_hits: int = MAX_HITS, fixed: bool = False) -> list[Hit]:
        for backend in self._order():
            try:
                hits = getattr(self, f"_{backend}")(pattern, globs, max_hits,
                                                    fixed)
                if hits is not None:
                    return hits[:max_hits]
            except Exception:
                continue
        return []

    def _order(self) -> list[str]:
        return {"rg": ["rg", "git_grep", "python"],
                "git-grep": ["git_grep", "python"],
                "python": ["python"]}[self.backend]

    # -- backends ----------------------------------------------------------
    def _rg(self, pattern, globs, max_hits, fixed):
        parts = ["rg", "--json", "--max-count", str(max_hits), "--no-heading"]
        if fixed:
            parts.append("--fixed-strings")
        for d in SKIP_DIRS:
            parts += ["--glob", f"!{d}/**"]
        for g in globs or []:
            parts += ["--glob", g]
        parts += ["--", _shq(pattern), "."]
        r = run(" ".join(parts), self.repo, timeout=30, check_deny=False)
        if r.exit_code not in (0, 1):
            return None
        hits = []
        for line in r.stdout.splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "match":
                continue
            d = rec["data"]
            hits.append(Hit(d["path"]["text"], d["line_number"],
                            d["lines"]["text"].rstrip("\n")))
        return hits

    def _pcre_ok(self) -> bool:
        if self._pcre is None:
            r = run("git grep -P -n -I -- 'a' -- /dev/null", self.repo,
                    timeout=10, check_deny=False)
            self._pcre = "not built with PCRE" not in (r.stderr or "")
        return self._pcre

    def _git_grep(self, pattern, globs, max_hits, fixed):
        if fixed:
            flag = "-F"
        elif PCRE_ONLY.search(pattern):
            if not self._pcre_ok():
                return None          # fall through to the Python backend
            flag = "-P"
        else:
            flag = "-E"
        cmd = f"git grep -n -I {flag} -- {_shq(pattern)}"
        r = run(cmd, self.repo, timeout=30, check_deny=False)
        if r.exit_code not in (0, 1):
            return None
        hits = []
        for line in r.stdout.splitlines()[:max_hits * 2]:
            parts = line.split(":", 2)
            if len(parts) != 3:
                continue
            path, num, text = parts
            if _skip(path) or not _glob_ok(path, globs):
                continue
            try:
                hits.append(Hit(path, int(num), text))
            except ValueError:
                continue
        return hits

    def _python(self, pattern, globs, max_hits, fixed):
        rx = re.compile(re.escape(pattern) if fixed else pattern)
        hits = []
        for path in self._walk():
            rel = str(path.relative_to(self.repo))
            if not _glob_ok(rel, globs):
                continue
            try:
                text = path.read_text("utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(Hit(rel, i, line))
                    if len(hits) >= max_hits:
                        return hits
        return hits

    def _walk(self):
        for path in self.repo.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.repo)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            if path.suffix.lower() in SKIP_SUFFIX:
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield path

    def files(self) -> list[str]:
        """Tracked files, or a filtered walk when git is unavailable."""
        r = run("git ls-files", self.repo, timeout=30, check_deny=False)
        if r.ok and r.stdout.strip():
            return [f for f in r.stdout.splitlines()
                    if f and not _skip(f)
                    and Path(f).suffix.lower() not in SKIP_SUFFIX]
        return [str(p.relative_to(self.repo)) for p in self._walk()]


def _skip(path: str) -> bool:
    return any(part in SKIP_DIRS for part in Path(path).parts)


def _glob_ok(path: str, globs: list[str] | None) -> bool:
    if not globs:
        return True
    from fnmatch import fnmatch
    return any(fnmatch(path, g) for g in globs)


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
