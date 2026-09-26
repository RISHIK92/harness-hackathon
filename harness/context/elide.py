"""Stage-1 compaction: deterministic elision (SPEC.md 6.3).

Rule-based elision is staged BEFORE any model summarization -- it is the
strongest strategy measured, and it prevents overflow for free.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PAYLOAD_CAP = 4096
HEAD_LINES = 60
TAIL_LINES = 20
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PROGRESS = re.compile(r"^[\s\.\-=#>]*\d+%[\s\.\-=#>]*$")


@dataclass
class Block:
    """One renderable unit of context."""
    kind: str
    key: str
    text: str
    pinned: bool = False

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def estimate_tokens(text: str) -> int:
    """~4 characters per token. Deliberately crude and deliberately cheap."""
    return max(1, len(text) // 4)


def clean(text: str) -> str:
    """Strip ANSI, progress bars, and runs of identical lines."""
    if not text:
        return ""
    text = ANSI.sub("", text)
    out: list[str] = []
    previous = None
    repeats = 0
    for line in text.splitlines():
        if PROGRESS.match(line):
            continue
        if line == previous:
            repeats += 1
            continue
        if repeats:
            out.append(f"    ... x{repeats + 1}")
            repeats = 0
        out.append(line)
        previous = line
    if repeats:
        out.append(f"    ... x{repeats + 1}")
    return "\n".join(out)


def truncate(text: str, head: int = HEAD_LINES, tail: int = TAIL_LINES) -> str:
    """Head + tail with a marker. Never cuts mid-function when it can help it."""
    lines = text.splitlines()
    if len(lines) <= head + tail:
        return text
    elided = len(lines) - head - tail
    cut = head
    for i in range(head, min(head + 12, len(lines) - tail)):
        if not lines[i].strip() or lines[i][:1] not in (" ", "\t"):
            cut = i                   # prefer a dedent/blank boundary
            break
    return "\n".join(lines[:cut]
                     + [f"    ... {elided} lines elided ..."]
                     + lines[-tail:])


def collapse_test_output(text: str, max_frames: int = 15) -> str:
    """Command, counts, and only the failing assertions and last frames."""
    lines = clean(text).splitlines()
    keep: list[str] = []
    in_failure = False
    frames = 0
    for line in lines:
        low = line.lower()
        if re.search(r"(=+ (FAILURES|ERRORS) =+)", line):
            in_failure = True
            keep.append(line)
            continue
        if re.search(r"\d+ (passed|failed|error|skipped)", low):
            keep.append(line)
            continue
        if line.startswith(("FAILED", "ERROR", "PASSED")):
            keep.append(line)
            continue
        if in_failure:
            if line.strip().startswith(("E ", "assert", "File \"", ">")):
                keep.append(line)
                frames += 1
            elif frames and frames < max_frames:
                keep.append(line)
                frames += 1
            if frames >= max_frames:
                in_failure = False
                frames = 0
    if not keep:
        return truncate(clean(text), 20, 20)
    return "\n".join(keep[:200])


def elide(blocks: list[Block], budget: int) -> list[Block]:
    """Apply the seven rules, then drop from the bottom until we fit."""
    # 1 + 2: drop duplicate / superseded blocks, newest wins.
    seen: dict[str, int] = {}
    for i, b in enumerate(blocks):
        if b.pinned:
            continue
        seen[b.key] = i
    deduped = [b for i, b in enumerate(blocks)
               if b.pinned or seen.get(b.key) == i]

    out: list[Block] = []
    for b in deduped:
        text = b.text
        if not b.pinned:
            # 4: failed/empty results become a marker.
            if not text.strip():
                text = "(no output)"
            # 5: test output collapses to counts + failures.
            elif b.kind == "test_output":
                text = collapse_test_output(text)
            # 3 + 6: cap size, strip noise.
            elif len(text) > PAYLOAD_CAP:
                text = truncate(clean(text))
            else:
                text = clean(text)
        out.append(Block(b.kind, b.key, text, b.pinned))

    # Drop from the bottom of the non-pinned tail until we fit.
    while sum(b.tokens for b in out) > budget:
        idx = next((i for i, b in enumerate(out) if not b.pinned), None)
        if idx is None:
            break
        out.pop(idx)
    return out
