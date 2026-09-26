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

from .formats import LINE_RANGE, SEARCH_REPLACE, WHOLE_FILE, Edit

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


def apply_all(repo: Path, edits: list[Edit]) -> list[Applied]:
    """Stage every edit in memory, then write. Atomic across files."""
    staged: dict[str, tuple[str, str]] = {}
    for i, edit in enumerate(edits):
        target = Path(repo) / edit.path
        if not target.is_file():
            raise EditFailure("apply", f"{edit.path} does not exist",
                              hunk_index=i, path=edit.path)
        before = staged[edit.path][1] if edit.path in staged else \
            target.read_text("utf-8", errors="replace")
        original = staged[edit.path][0] if edit.path in staged else before
        try:
            after = render(edit, before)
        except EditFailure as exc:
            exc.hunk_index = i
            exc.path = exc.path or edit.path
            raise
        staged[edit.path] = (original, after)

    out = []
    for path, (before, after) in staged.items():
        target = Path(repo) / path
        target.write_text(after, encoding="utf-8")
        added, removed = _count(before, after)
        out.append(Applied(path, before, after, added, removed))
    return out


def rollback(repo: Path, applied: list[Applied]) -> None:
    for a in applied:
        try:
            (Path(repo) / a.path).write_text(a.before, encoding="utf-8")
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
