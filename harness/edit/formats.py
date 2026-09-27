"""Edit formats, ranked by mechanical reliability (SPEC.md 8.2).

Unified diff is deliberately NOT implemented: it is the worst performer on
both success rate and token cost, because exact line numbers and context
counts must be right.  The three below trade tokens for apply-rate, and the
choice is a function of the model tier and of how many attempts have failed.
"""
from __future__ import annotations

from dataclasses import dataclass

LINE_RANGE = "LINE_RANGE"
SEARCH_REPLACE = "SEARCH_REPLACE"
WHOLE_FILE = "WHOLE_FILE"

ORDER = (SEARCH_REPLACE, LINE_RANGE, WHOLE_FILE)
WHOLE_FILE_MAX_LINES = 150


# What an edit does to a path. Changing a file is by far the common case, but
# a fix that needs a new module -- or that removes a dead one -- is ordinary
# work, and a harness that cannot do it hands the job back to a human.
EDIT = "edit"
CREATE = "create"
DELETE = "delete"
RENAME = "rename"


@dataclass
class Edit:
    """One change to one file."""
    path: str
    fmt: str
    search: str = ""          # SEARCH_REPLACE
    replace: str = ""         # SEARCH_REPLACE / LINE_RANGE / WHOLE_FILE
    start: int = 0            # LINE_RANGE, 1-indexed inclusive
    end: int = 0
    op: str = EDIT
    dest: str = ""            # RENAME target

    def summary(self) -> str:
        if self.op == CREATE:
            return f"CREATE {self.path}"
        if self.op == DELETE:
            return f"DELETE {self.path}"
        if self.op == RENAME:
            return f"RENAME {self.path} -> {self.dest}"
        where = self.path
        if self.fmt == LINE_RANGE:
            where += f":{self.start}-{self.end}"
        return f"{self.fmt} {where}"


def choose(tier: str, file_lines: int, conservative: bool = False,
           failures: int = 0) -> str:
    """T0 gets the format that cannot fail to locate; T1/T2 get the terse one."""
    if conservative:
        return LINE_RANGE
    if failures >= 2:
        return (WHOLE_FILE if file_lines and file_lines <= WHOLE_FILE_MAX_LINES
                else LINE_RANGE)
    if failures == 1:
        return LINE_RANGE
    if tier == "T0":
        return LINE_RANGE
    return SEARCH_REPLACE


def de_escalate(current: str, file_lines: int) -> str:
    """Next format down the reliability ladder."""
    if current == SEARCH_REPLACE:
        return LINE_RANGE
    if current == LINE_RANGE and file_lines and file_lines <= WHOLE_FILE_MAX_LINES:
        return WHOLE_FILE
    return LINE_RANGE


INSTRUCTIONS = {
    SEARCH_REPLACE: """\
For each change, emit a block in exactly this form:

<<<<<<< SEARCH {path}
(the exact existing lines, copied verbatim, including indentation)
=======
(the replacement lines)
>>>>>>> REPLACE

The SEARCH text must match the file byte for byte and must appear exactly
once. Emit nothing else.""",

    LINE_RANGE: """\
Replace the numbered region shown above. Emit exactly one block:

<<<<<<< REPLACE {path}:{start}-{end}
(the full replacement for those lines, without line numbers)
>>>>>>>

Emit nothing else.""",

    WHOLE_FILE: """\
Emit the complete file contents, unchanged except for your fix:

<<<<<<< FILE {path}
(the entire file)
>>>>>>>

Emit nothing else.""",
}

# Available in every format, because they are about paths, not about how a
# file's contents are expressed.
FILE_OPS = """\
To add, remove or move a file, use one of these instead:

<<<<<<< CREATE path/to/new_file.ext
(the entire contents of the new file)
>>>>>>>

<<<<<<< DELETE path/to/old_file.ext
>>>>>>>

<<<<<<< RENAME old/path.ext -> new/path.ext
>>>>>>>

Only create a file when the fix genuinely needs one."""


def instructions(fmt: str, path: str = "<path>", start: int = 0,
                 end: int = 0) -> str:
    return INSTRUCTIONS[fmt].format(path=path, start=start, end=end)
