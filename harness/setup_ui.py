"""The readiness card printed by `make setup` (SPEC.md 13, 32).

Never interactive and never blocking: an evaluator may run
`make setup && make run` in a script, and a setup that waits for a keystroke
hangs the whole evaluation.

Degraded items are shown rather than hidden. Seeing `! ripgrep  not found`
and the run working anyway is evidence the degradation matrix is real.
"""
from __future__ import annotations

import sys

from .logging_ui import Logger
from .selfcheck import check

DETAIL = {
    "coverage": "coverage localization",
    "ripgrep": "fast search",
    "tree-sitter": "AST navigation",
    "ctags": "symbol extraction",
    "pytest": "harness self-tests",
    "git": "checkpoints and history",
}


def render(stream=None) -> int:
    log = Logger(stream=stream or sys.stdout)
    rows, fatal = check()
    t = log.theme

    log.banner("2.1")
    log.raw("")
    for item, status, detail in rows:
        text = detail or DETAIL.get(item, "")
        if not log.rich:
            mark = {"ok": "ok", "degraded": "degraded", "FATAL": "FATAL"}[status]
            log.raw(f"  {item:<14} {mark}" + (f"  {text}" if text else ""))
        elif status == "ok":
            log.ok(item, text)
        elif status == "degraded":
            log.step("warn", item, text, t.warn)
        else:
            log.fail(item, text)

    log.raw("")
    if fatal:
        if log.rich:
            log.fail("setup", "cannot continue: fix the items above")
        else:
            log.raw("setup cannot continue: fix the FATAL items above")
        return 1

    degraded = [r for r in rows if r[1] == "degraded"]
    if degraded:
        log.kv("note", f"{len(degraded)} optional tool(s) missing; every one "
                       f"degrades cleanly", t.dim)
    log.kv("ready", "next:  make run", t.bold)
    log.raw("")
    return 0


def main() -> int:
    return render()


if __name__ == "__main__":
    raise SystemExit(main())
