"""Parse model output into Edit objects, tolerantly (SPEC.md 8.3).

Models wrap blocks in code fences, add prose, and occasionally forget a
terminator.  All three are recoverable; an ambiguous anchor is not.
"""
from __future__ import annotations

import re

from .formats import LINE_RANGE, SEARCH_REPLACE, WHOLE_FILE, Edit


class EditParseError(Exception):
    def __init__(self, detail: str, raw: str = "") -> None:
        super().__init__(detail)
        self.detail = detail
        self.raw = raw[:2000]


FENCE = re.compile(r"^\s*```[\w]*\s*$", re.M)

# Path operations. Recognised whatever the edit format is, because they are
# about paths rather than about how a file's contents are expressed.
CREATE_BLOCK = re.compile(
    r"<{5,9}\s*CREATE\s+(?P<path>\S+)[^\n]*\n(?P<body>.*?)\n?>{5,9}[^\n]*",
    re.S)
DELETE_BLOCK = re.compile(
    r"<{5,9}\s*DELETE\s+(?P<path>\S+)[^\n]*\n?\s*>{5,9}[^\n]*", re.S)
RENAME_BLOCK = re.compile(
    r"<{5,9}\s*RENAME\s+(?P<path>\S+)\s*(?:->|=>|\s)\s*(?P<dest>\S+)"
    r"[^\n]*\n?\s*>{5,9}[^\n]*", re.S)

SR_BLOCK = re.compile(
    r"<{5,9}\s*SEARCH\s*(?P<path>[^\n]*)\n(?P<search>.*?)\n?"
    r"={5,9}\s*\n(?P<replace>.*?)\n?>{5,9}\s*(?:REPLACE)?[^\n]*",
    re.S)

LR_BLOCK = re.compile(
    r"<{5,9}\s*REPLACE\s+(?P<path>\S+?):(?P<start>\d+)-(?P<end>\d+)[^\n]*\n"
    r"(?P<replace>.*?)\n?>{5,9}[^\n]*",
    re.S)

FILE_BLOCK = re.compile(
    r"<{5,9}\s*FILE\s+(?P<path>\S+)[^\n]*\n(?P<replace>.*?)\n?>{5,9}[^\n]*",
    re.S)


def strip_fences(text: str) -> str:
    return FENCE.sub("", text or "")


def parse(text: str, fmt: str, default_path: str = "",
          default_range: tuple = (0, 0)) -> list[Edit]:
    """Parse, repairing the malformations models actually produce."""
    if not text or not text.strip():
        raise EditParseError("empty reply")
    body = strip_fences(text)

    # Path operations first, and removed from the body, so a CREATE block is
    # never also read as a content edit to a file that does not exist.
    ops, body = _parse_file_ops(body)

    if fmt == SEARCH_REPLACE:
        edits = _parse_sr(body, default_path)
    elif fmt == LINE_RANGE:
        edits = _parse_lr(body, default_path, default_range)
    else:
        edits = _parse_file(body, default_path)

    if not edits and not ops:
        raise EditParseError(f"no {fmt} block found", text)
    return ops + edits


def _parse_file_ops(body: str) -> tuple[list, str]:
    """Pull CREATE / DELETE / RENAME out of the reply.

    Returns the operations and the body with them removed, so the remaining
    text can be parsed as ordinary content edits.
    """
    from .formats import CREATE, DELETE, RENAME, WHOLE_FILE
    ops: list[Edit] = []
    for pattern, kind in ((RENAME_BLOCK, RENAME), (CREATE_BLOCK, CREATE),
                          (DELETE_BLOCK, DELETE)):
        for m in pattern.finditer(body):
            path = _clean_path(m.group("path"), "")
            if not path:
                continue
            edit = Edit(path=path, fmt=WHOLE_FILE, op=kind)
            if kind == CREATE:
                edit.replace = m.group("body")
            elif kind == RENAME:
                edit.dest = _clean_path(m.group("dest"), "")
                if not edit.dest:
                    continue
            ops.append(edit)
        body = pattern.sub("", body)
    return ops, body


def _clean_path(raw: str, default: str) -> str:
    p = (raw or "").strip().strip("`\"'").lstrip("./")
    return p or default


def _parse_sr(body: str, default_path: str) -> list[Edit]:
    out = []
    for m in SR_BLOCK.finditer(body):
        search = m.group("search")
        if not search.strip():
            continue
        out.append(Edit(path=_clean_path(m.group("path"), default_path),
                        fmt=SEARCH_REPLACE, search=search,
                        replace=m.group("replace")))
    if out:
        return out
    return _repair_sr(body, default_path)


def _repair_sr(body: str, default_path: str) -> list[Edit]:
    """An unterminated final block is recoverable; a missing divider is not."""
    start = re.search(r"<{5,9}\s*SEARCH\s*([^\n]*)\n", body)
    if not start:
        return []
    rest = body[start.end():]
    divider = re.search(r"\n={5,9}\s*\n", rest)
    if not divider:
        return []
    search = rest[:divider.start()]
    replace = rest[divider.end():]
    replace = re.split(r">{5,9}", replace)[0]
    if not search.strip():
        return []
    return [Edit(path=_clean_path(start.group(1), default_path),
                 fmt=SEARCH_REPLACE, search=search,
                 replace=replace.rstrip("\n"))]


def _parse_lr(body: str, default_path: str,
              default_range: tuple) -> list[Edit]:
    out = []
    for m in LR_BLOCK.finditer(body):
        out.append(Edit(path=_clean_path(m.group("path"), default_path),
                        fmt=LINE_RANGE,
                        start=int(m.group("start")), end=int(m.group("end")),
                        replace=_strip_numbers(m.group("replace"))))
    if out:
        return out
    # The model answered with bare replacement text: accept it against the
    # range the harness supplied, which is the whole point of this format.
    if default_path and default_range and default_range[1]:
        cleaned = _strip_markers(body)
        if cleaned.strip():
            return [Edit(path=default_path, fmt=LINE_RANGE,
                         start=default_range[0], end=default_range[1],
                         replace=_strip_numbers(cleaned))]
    return []


def _parse_file(body: str, default_path: str) -> list[Edit]:
    out = []
    for m in FILE_BLOCK.finditer(body):
        out.append(Edit(path=_clean_path(m.group("path"), default_path),
                        fmt=WHOLE_FILE, replace=m.group("replace")))
    if out:
        return out
    cleaned = _strip_markers(body)
    if default_path and cleaned.strip():
        return [Edit(path=default_path, fmt=WHOLE_FILE, replace=cleaned)]
    return []


NUMBERED = re.compile(r"^\s*\d+\s*\|\s?", re.M)
MARKER = re.compile(r"^\s*(<{5,9}|>{5,9}|={5,9}).*$", re.M)


def _strip_numbers(text: str) -> str:
    """Models echo the `12 | code` gutter the harness printed."""
    lines = text.splitlines()
    if lines and sum(bool(NUMBERED.match(ln)) for ln in lines) > len(lines) / 2:
        return NUMBERED.sub("", text)
    return text


def _strip_markers(text: str) -> str:
    return MARKER.sub("", text).strip("\n")
