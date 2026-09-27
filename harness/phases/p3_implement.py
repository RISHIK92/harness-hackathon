"""Phase 3: implementation (FR-22..FR-26, SPEC.md 7.4).

The implementer receives the most constrained context of any phase and it
does NOT receive the investigation history: that history is exploratory
reasoning, and it muddies implementation.  What it needs is what to change
and how the surrounding code is written.
"""
from __future__ import annotations

from pathlib import Path

from ..context.assemble import system_prompt, wrap_untrusted
from ..edit import formats as F
from ..edit.apply import Applied, EditFailure, apply_all, rollback
from ..edit.parse import EditParseError, parse
from ..edit.validate import validate
from ..repo.snippets import (imports_block, neighbours, read_window,
                             style_profile, symbols)

PROMPT = """\
You are implementing a specific, scoped fix. Do not explain, do not preface.

ROOT CAUSE:        {root_cause}
FIX DESCRIPTION:   {description}
FILE:              {path}
TARGET:            {target}
ESTIMATED SIZE:    {estimate} lines changed

STYLE REFERENCE - the functions adjacent to your target. Match these:
{neighbours}

MEASURED STYLE OF THIS FILE (conform exactly):
{style}

THE CODE TO CHANGE:
{target_code}

CONSTRAINTS - any violation causes rejection and a retry:
  1  Match the style reference exactly: indentation, naming, comments, errors.
  2  No new abstractions, helper functions, classes or files.
  3  No new dependencies or imports beyond what this file already imports.
  4  No defensive code - no try/except, no null guards - that the surrounding
     functions do not already use.
  5  No TODO, FIXME, debug prints, logging, or commented-out code.
  6  No comment explaining the fix. No docstring rewrites.
  7  Do not reformat, reorder or re-indent any line you are not fixing.
  8  Touch only: {allowed}. Never: {forbidden}.
  9  Produce the smallest change that resolves the stated root cause.
 10  If your change exceeds {cap} lines, stop and reconsider - you are almost
     certainly changing too much.

CURRENT IMPORTS (do not add to these):
{imports}

OUTPUT FORMAT:
{output_format}"""


def _plan_has_new_files(plan) -> bool:
    """Only offer the file operations when the plan actually calls for one.

    Showing them unconditionally invites a model to create a file instead of
    making the small edit that was asked for.
    """
    return any(getattr(fi, "is_new", False) for fi in plan.files_to_change)


class Implementation:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.failures = 0

    def run(self, issue, root_cause, plan, feedback: str = "") -> tuple:
        """Returns (applied, flags). Raises EditFailure when unrecoverable."""
        c = self.ctx
        # The phase header is emitted by the orchestrator, which knows the
        # cycle number.
        if not plan.files_to_change:
            raise EditFailure(
                "scope", "the plan names no file to change; nothing to "
                         "implement", path="")
        target = plan.files_to_change[0]
        path = target.path
        full = c.repo / path
        file_lines = len(full.read_text("utf-8", errors="replace").splitlines())
        fmt = F.choose(c.cfg.effective_tier, file_lines, c.cfg.conservative,
                       self.failures)

        span = self._span(root_cause, path, target.symbol)
        if fmt == F.LINE_RANGE and not span[1]:
            # No resolvable range: LINE_RANGE would emit `path:0-0`, which can
            # never apply. Use a format that does not need one.
            fmt = (F.WHOLE_FILE if file_lines <= F.WHOLE_FILE_MAX_LINES
                   else F.SEARCH_REPLACE)
        messages = self._messages(issue, root_cause, plan, target, fmt, span,
                                  feedback)
        where = path + (f":{span[0]}-{span[1]}" if span[1] else "")
        c.log.computed("format", f"{fmt}  {where}",
                       plain=f"format {fmt}  target {where}")

        reply = c.router.call("p3_implement", messages, "P3",
                              max_tokens=3000, cache_prefix=1)
        try:
            edits = parse(reply.text, fmt, default_path=path,
                          default_range=span)
        except EditParseError as exc:
            self.failures += 1
            raise EditFailure("parse", exc.detail, path=path)

        for e in edits:
            c.log.cont(e.summary())

        try:
            applied = apply_all(c.repo, edits)
        except EditFailure:
            # Counting this is what makes the format ladder de-escalate; not
            # counting it retries the identical edit until the cycle cap.
            self.failures += 1
            raise
        try:
            flags = validate(c.repo, applied, plan, c.cfg, c.toolchain)
        except EditFailure:
            rollback(c.repo, applied)
            self.failures += 1
            raise

        added = sum(a.added for a in applied)
        removed = sum(a.removed for a in applied)
        c.log.ok("applied", f"+{added} -{removed} across "
                            f"{len(applied)} file(s)",
                 plain=f"applied: +{added} -{removed} across "
                       f"{len(applied)} file(s)")
        if flags:
            c.log.step("warn", "hygiene", ", ".join(flags), c.log.theme.warn,
                       plain="hygiene flags: " + ", ".join(flags))
        c.events.append("edit_applied", "P3",
                        {"files": [a.path for a in applied],
                         "added": added, "removed": removed, "flags": flags,
                         "format": fmt},
                        summary=f"+{added} -{removed}")
        return applied, flags

    # -- context -----------------------------------------------------------
    def _span(self, root_cause, path: str, symbol: str | None) -> tuple:
        """A line range, validated against the file it refers to.

        The range comes from the model, so it can point past the end of the
        file. Handing that to LINE_RANGE produces an edit that can never
        apply, and the harness would retry it unchanged.
        """
        try:
            total = len((self.ctx.repo / path).read_text(
                "utf-8", errors="replace").splitlines())
        except OSError:
            total = 0

        for f in root_cause.files:
            if f.path == path and f.lines and f.lines[1]:
                start, end = max(1, f.lines[0]), f.lines[1]
                if total and start <= total:
                    return (start, min(end, total))
                break          # out of range: fall through to the symbol

        if symbol:
            for sym in symbols(self.ctx.repo / path):
                if sym.name == symbol:
                    return (sym.start, min(sym.end, total or sym.end))
        return (0, 0)

    def _messages(self, issue, root_cause, plan, target, fmt, span,
                  feedback) -> list:
        c = self.ctx
        path = target.path
        full = c.repo / path
        style = style_profile(full)
        sym = target.symbol or ""

        if fmt == F.LINE_RANGE and span[1]:
            code = read_window(full, span[0], 0, span[1] - span[0])
        elif sym:
            from ..repo.snippets import read_symbol
            code, _ = read_symbol(full, sym)
            code = code or read_window(full, span[0] or 1, 4, 30)
        else:
            code = read_window(full, span[0] or 1, 4, 40)

        body = PROMPT.format(
            root_cause=root_cause.statement,
            description=plan.fix_description,
            path=path,
            target=(f"{sym} at lines {span[0]}-{span[1]}" if span[1]
                    else sym or "the region shown below"),
            estimate=plan.estimated_lines_changed,
            neighbours=neighbours(full, sym, k=3) or "(no adjacent functions)",
            style=style.render(),
            target_code=code,
            allowed=", ".join(sorted(plan.allowed)) or path,
            forbidden=", ".join(sorted(plan.forbidden)[:4]) or "tests/",
            cap=int(plan.estimated_lines_changed * 1.3),
            imports=imports_block(full) or "(none)",
            output_format=F.instructions(fmt, path, span[0], span[1])
            + (f"\n\n{F.FILE_OPS}" if _plan_has_new_files(plan) else ""),
        )
        if feedback:
            body += f"\n\nYOUR PREVIOUS ATTEMPT WAS REJECTED:\n{feedback}"

        # Deliberately absent: the trajectory, the hypotheses, the rejected
        # alternatives. The implementer needs what and how, not why.
        return [
            {"role": "system", "content": system_prompt(
                "implementation", c.cfg.effective_tier,
                "You write the smallest correct change. You do not explain.")},
            {"role": "user", "content": body},
        ]
