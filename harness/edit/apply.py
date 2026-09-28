"""Locating and applying edits (SPEC.md 8.3, 26.3).

The anchor ladder relaxes matching step by step but NEVER guesses: an
ambiguous match is a typed failure.  A multi-file edit set applies atomically,
so a half-applied change can never reach the verifier.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

from ..repo.paths import in_repo
from .formats import (CREATE, DELETE, EDIT, LINE_RANGE, RENAME,
                      SEARCH_REPLACE, WHOLE_FILE, Edit)

FUZZ_THRESHOLD = 0.92


@dataclass
class EditFailure(Exception):
    stage: str                 # parse | locate | apply | syntax | scope | size
    detail: str
    hunk_index: int = 0
    path: str = ""

    def __str__(self) -> str:
        return f"[{self.stage}] {self.detail}"

    def feedback(self) -> str:
        """The specific correction request. Never 'that didn't work'."""
        if self.stage == "locate":
            return (f"The SEARCH text for {self.path} was not found exactly "
                    f"once. {self.detail} Re-read the file and copy the lines "
                    f"byte for byte, including indentation.")
        if self.stage == "syntax":
            return (f"The edit left {self.path} unparseable: {self.detail} "
                    "Return a corrected replacement.")
        if self.stage == "scope":
            return (f"{self.detail} Only the files named in the plan may be "
                    "modified.")
        if self.stage == "size":
            return (f"{self.detail} Produce a smaller change that fixes only "
                    "the stated root cause.")
        return self.detail


@dataclass
class Applied:
    path: str
    before: str
    after: str
    added: int
    removed: int
    op: str = EDIT
    existed: bool = True      # so a rollback knows whether to delete
    dest: str = ""


def locate(haystack: str, needle: str) -> tuple[int, int]:
    """Return (start, end) character offsets, or raise. Unique match required."""
    if not needle:
        raise EditFailure("locate", "empty SEARCH text")

    # 1. exact
    hits = _all_offsets(haystack, needle)
    if len(hits) == 1:
        return hits[0], hits[0] + len(needle)
    if len(hits) > 1:
        raise EditFailure("locate",
                          f"SEARCH text appears {len(hits)} times; it must be "
                          f"unique. Include more surrounding context.")

    # 2. whitespace-normalized per line
    start = _match_normalized(haystack, needle, _norm_ws)
    if start is not None:
        return start

    # 3. indentation-agnostic
    start = _match_normalized(haystack, needle, _norm_indent)
    if start is not None:
        return start

    # 4. fuzzy, still requiring a single clear winner
    start = _match_fuzzy(haystack, needle)
    if start is not None:
        return start

    raise EditFailure("locate", "SEARCH text not found in the file")


def _all_offsets(haystack: str, needle: str) -> list[int]:
    out, i = [], haystack.find(needle)
    while i != -1:
        out.append(i)
        i = haystack.find(needle, i + 1)
    return out


def _norm_ws(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def _norm_indent(line: str) -> str:
    return line.strip()


def _match_normalized(haystack: str, needle: str, norm) -> tuple | None:
    hay_lines = haystack.splitlines(keepends=True)
    needle_lines = [ln for ln in needle.splitlines() if ln.strip()]
    if not needle_lines:
        return None
    target = [norm(ln) for ln in needle_lines]
    n = len(target)

    matches = []
    norm_hay = [norm(ln) for ln in hay_lines]
    for i in range(len(hay_lines) - n + 1):
        window = [x for x in norm_hay[i:i + n]]
        if window == target:
            matches.append(i)
    if len(matches) != 1:
        return None
    i = matches[0]
    start = sum(len(ln) for ln in hay_lines[:i])
    end = start + sum(len(ln) for ln in hay_lines[i:i + n])
    return start, end


def _match_fuzzy(haystack: str, needle: str) -> tuple | None:
    hay_lines = haystack.splitlines(keepends=True)
    n = max(1, len(needle.splitlines()))
    best, best_ratio, runner_up = None, 0.0, 0.0
    for i in range(len(hay_lines) - n + 1):
        chunk = "".join(hay_lines[i:i + n])
        ratio = difflib.SequenceMatcher(None, chunk, needle).ratio()
        if ratio > best_ratio:
            runner_up, best_ratio, best = best_ratio, ratio, i
        elif ratio > runner_up:
            runner_up = ratio
    if best is None or best_ratio < FUZZ_THRESHOLD:
        return None
    if runner_up >= best_ratio - 0.02:
        raise EditFailure("locate",
                          "two regions match the SEARCH text about equally "
                          "well; include more context")
    start = sum(len(ln) for ln in hay_lines[:best])
    end = start + sum(len(ln) for ln in hay_lines[best:best + n])
    return start, end


def render(edit: Edit, original: str) -> str:
    """Produce the new file contents for one edit. Raises EditFailure."""
    if edit.fmt == WHOLE_FILE:
        return _ensure_newline(edit.replace)

    if edit.fmt == LINE_RANGE:
        lines = original.splitlines(keepends=True)
        if edit.start < 1 or edit.end > len(lines) or edit.start > edit.end:
            raise EditFailure(
                "locate",
                f"line range {edit.start}-{edit.end} is outside the file "
                f"(1-{len(lines)})", path=edit.path)
        replacement = edit.replace
        if replacement and not replacement.endswith("\n"):
            replacement += "\n"
        return ("".join(lines[:edit.start - 1]) + replacement
                + "".join(lines[edit.end:]))

    start, end = locate(original, edit.search)
    return original[:start] + edit.replace + original[end:]


def _ensure_newline(text: str) -> str:
    return text if text.endswith("\n") or not text else text + "\n"


def _contained(repo: Path, path: str, index: int) -> None:
    """Refuse a path outside the repository BEFORE anything is written.

    The parser used to strip every leading `.` and `/`, which happened to
    turn `../x` into `x`; a `..` further in (`src/../../x`) and a symlink out
    of the tree were never caught, and the scope check that would have
    caught them runs after the write. `.git` is refused too: a file written
    there is a hook the next commit runs.
    """
    if in_repo(repo, path) is None:
        raise EditFailure("apply", f"{path} is outside the repository; "
                                   f"only paths inside it can be changed",
                          hunk_index=index, path=path)


def apply_all(repo: Path, edits: list[Edit]) -> list[Applied]:
    """Stage every edit in memory, then write. Atomic across files."""
    staged: dict[str, tuple[str, str]] = {}
    ops: list[Applied] = []

    for i, edit in enumerate(edits):
        _contained(repo, edit.path, i)
        if edit.op == RENAME:
            _contained(repo, edit.dest, i)

    # Path operations are staged first and separately: they decide whether a
    # file exists at all, which every content edit below then depends on.
    content = []
    for i, edit in enumerate(edits):
        if edit.op == EDIT:
            content.append((i, edit))
            continue
        ops.append(_stage_path_op(repo, edit, i))

    created = {a.path for a in ops if a.op == CREATE}
    renamed = {a.dest for a in ops if a.op == RENAME}

    for i, edit in content:
        target = Path(repo) / edit.path
        # A file created in the same reply is a legitimate edit target, even
        # though it is not on disk yet.
        if not target.is_file() and edit.path not in created \
                and edit.path not in renamed:
            raise EditFailure("apply", f"{edit.path} does not exist",
                              hunk_index=i, path=edit.path)
        if edit.path in staged:
            before = staged[edit.path][1]
        elif target.is_file():
            before = target.read_text("utf-8", errors="replace")
        else:
            before = next((a.after for a in ops
                           if edit.path in (a.path, a.dest)), "")
        original = staged[edit.path][0] if edit.path in staged else before
        try:
            after = render(edit, before)
        except EditFailure as exc:
            exc.hunk_index = i
            exc.path = exc.path or edit.path
            raise
        staged[edit.path] = (original, after)

    out = list(ops)
    for a in ops:
        _commit_path_op(repo, a)
    for path, (before, after) in staged.items():
        target = Path(repo) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(after, encoding="utf-8")
        added, removed = _count(before, after)
        out.append(Applied(path, before, after, added, removed))
    return out


def _stage_path_op(repo: Path, edit: Edit, index: int) -> Applied:
    """Validate a create/delete/rename without touching the disk yet."""
    target = Path(repo) / edit.path
    if edit.op == CREATE:
        if target.is_file():
            # Creating over an existing file loses its contents silently.
            raise EditFailure("apply", f"{edit.path} already exists; edit it "
                                       f"instead of creating it",
                              hunk_index=index, path=edit.path)
        body = _ensure_newline(edit.replace)
        return Applied(edit.path, "", body, len(body.splitlines()), 0,
                       op=CREATE, existed=False)

    if edit.op == DELETE:
        if not target.is_file():
            raise EditFailure("apply", f"{edit.path} does not exist",
                              hunk_index=index, path=edit.path)
        before = target.read_text("utf-8", errors="replace")
        return Applied(edit.path, before, "", 0, len(before.splitlines()),
                       op=DELETE)

    if edit.op == RENAME:
        if not target.is_file():
            raise EditFailure("apply", f"{edit.path} does not exist",
                              hunk_index=index, path=edit.path)
        if (Path(repo) / edit.dest).exists():
            raise EditFailure("apply", f"{edit.dest} already exists",
                              hunk_index=index, path=edit.path)
        before = target.read_text("utf-8", errors="replace")
        return Applied(edit.path, before, before, 0, 0, op=RENAME,
                       dest=edit.dest)

    raise EditFailure("apply", f"unknown operation {edit.op!r}",
                      hunk_index=index, path=edit.path)


def _commit_path_op(repo: Path, a: Applied) -> None:
    target = Path(repo) / a.path
    if a.op == CREATE:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(a.after, encoding="utf-8")
    elif a.op == DELETE:
        target.unlink(missing_ok=True)
    elif a.op == RENAME:
        dest = Path(repo) / a.dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        target.rename(dest)


def rollback(repo: Path, applied: list[Applied]) -> None:
    """Undo in reverse, so a rename is put back before its old path is
    rewritten."""
    for a in reversed(applied):
        try:
            target = Path(repo) / a.path
            if a.op == RENAME:
                moved = Path(repo) / a.dest
                if moved.is_file():
                    moved.rename(target)
                continue
            if a.op == CREATE or not a.existed:
                target.unlink(missing_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(a.before, encoding="utf-8")
        except OSError:
            pass


def _count(before: str, after: str) -> tuple[int, int]:
    diff = difflib.unified_diff(before.splitlines(), after.splitlines(),
                                lineterm="", n=0)
    added = removed = 0
    for line in diff:
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed
