"""Repository state, checkpoints and restore (SPEC.md 11.2, FR-3).

The working tree is always recoverable, and the harness never exits with a
worse tree than its best attempt.
"""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..verify.runner import run


@dataclass
class Checkpoint:
    ref: str
    label: str
    is_git: bool
    snapshot: str | None = None      # directory path for the non-git fallback


class Workspace:
    def __init__(self, repo_path: Path, log=None) -> None:
        self.path = Path(repo_path).resolve()
        self.log = log
        self.is_git = self._detect_git()
        self._excluded = False

    # -- git ---------------------------------------------------------------
    def _detect_git(self) -> bool:
        r = run("git rev-parse --is-inside-work-tree", self.path, timeout=10,
                check_deny=False)
        return r.ok and r.stdout.strip() == "true"

    def git(self, args: str, timeout: float = 60.0, truncate: bool = True):
        # Harness-issued git commands bypass the deny list: the list exists to
        # constrain MODEL-issued commands, and the harness owns checkpointing.
        return run(f"git {args}", self.path, timeout=timeout,
                   check_deny=False, truncate=truncate)

    def head(self) -> str:
        r = self.git("rev-parse HEAD")
        return r.stdout.strip() if r.ok else ""

    EXCLUDES = (".harness/", ".worktrees/", "__pycache__/", "*.pyc",
                ".coverage", ".pytest_cache/")

    def exclude_harness_dir(self) -> None:
        """Our artifacts go in .git/info/exclude, never the repo's .gitignore.

        Each pattern is checked individually: an exclude file written by an
        earlier run must still pick up patterns added since.
        """
        if not self.is_git or self._excluded:
            return
        info = self.path / ".git" / "info"
        try:
            info.mkdir(parents=True, exist_ok=True)
            f = info / "exclude"
            body = f.read_text("utf-8") if f.is_file() else ""
            present = {ln.strip() for ln in body.splitlines()}
            missing = [p for p in self.EXCLUDES if p not in present]
            if missing:
                f.write_text(body.rstrip("\n") + "\n" + "\n".join(missing)
                             + "\n", encoding="utf-8")
            self._excluded = True
        except OSError:
            pass

    # -- state -------------------------------------------------------------
    def dirty(self) -> bool:
        if not self.is_git:
            return False
        return bool(self.git("status --porcelain").stdout.strip())

    def changed_files(self) -> list[str]:
        """Files WE changed.

        Running the suite leaves build artifacts (__pycache__, .coverage,
        compiled output) behind. A repository without a .gitignore reports
        them as untracked, and feeding a .pyc to the linter produces a
        spurious block, so they are filtered here rather than everywhere
        downstream.
        """
        from .search import SKIP_DIRS, SKIP_SUFFIX
        if not self.is_git:
            return []
        # Never truncated: this list is what C4 checks for unintended
        # changes, and a cut-off list would hide one.
        r = self.git("diff --name-only HEAD", truncate=False)
        files = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        untracked = self.git("ls-files --others --exclude-standard",
                             truncate=False)
        files += [ln.strip() for ln in untracked.stdout.splitlines()
                  if ln.strip()]

        out = []
        for f in set(files):
            path = Path(f)
            if f.startswith((".harness", ".worktrees")):
                continue
            # The self-written reproduction (verify/repro.py) lives at the
            # repository root so its imports are the ordinary ones. It is
            # scaffolding, not a deliverable: counting it here would fail C4
            # and put a model-written test in the diff.
            if path.stem.startswith("harness_repro") or \
                    path.name.startswith("test_harness_repro"):
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() in SKIP_SUFFIX or f.endswith(".coverage"):
                continue
            out.append(f)
        return sorted(out)

    def diff(self, stat: bool = False) -> str:
        if not self.is_git:
            return ""
        return self.git(f"diff{' --stat' if stat else ''} HEAD").stdout

    def diff_numstat(self) -> tuple[int, int]:
        """(added, removed) lines, excluding harness artifacts."""
        if not self.is_git:
            return (0, 0)
        added = removed = 0
        for line in self.git("diff --numstat HEAD").stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 3 or parts[2].startswith(".harness"):
                continue
            try:
                added += int(parts[0])
                removed += int(parts[1])
            except ValueError:
                pass            # binary file: "-"
        return added, removed

    # -- checkpoints -------------------------------------------------------
    def checkpoint(self, label: str) -> Checkpoint:
        self.exclude_harness_dir()
        if self.is_git:
            # `git stash create` needs the change in the index, but leaving it
            # staged means a plain `git diff` shows nothing -- an evaluator
            # would see an empty diff. Stage, snapshot, then unstage.
            self.git("add -A")     # honours .git/info/exclude
            r = self.git("stash create")
            ref = r.stdout.strip()
            self.git("reset -q")
            if not ref:
                ref = self.head()
            return Checkpoint(ref=ref, label=label, is_git=True)

        snap = tempfile.mkdtemp(prefix="harness-snap-")
        shutil.copytree(self.path, Path(snap) / "tree", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".harness", ".git",
                                                      "__pycache__", ".venv"))
        return Checkpoint(ref="", label=label, is_git=False, snapshot=snap)

    def restore(self, cp: Checkpoint) -> bool:
        if cp.is_git:
            if not cp.ref:
                return False
            r = self.git(f"checkout {cp.ref} -- .")
            return r.ok
        if not cp.snapshot:
            return False
        src = Path(cp.snapshot) / "tree"
        for item in src.rglob("*"):
            if item.is_file():
                dst = self.path / item.relative_to(src)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dst)
        return True

    def revert_all(self) -> None:
        """Return the tree to HEAD. Used on exit 4 / 5 and between attempts.

        `git add -A` during checkpointing stages everything, so an unstage
        must precede the checkout or the tree stays dirty.
        """
        if not self.is_git:
            return
        self.git("reset -q")
        self.git("checkout -- .")
        self.git("clean -qfd -e .harness -e .worktrees", timeout=30)
