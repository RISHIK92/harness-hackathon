"""Readiness report printed by `make setup` (SPEC.md 13).

Never exits non-zero for a missing optional dependency -- an optional wheel
failure must degrade the harness, not fail setup (NFR-2, NFR-3).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REQUIRED_PY = (3, 10)


def _cmd_ok(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=5, check=False)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def check() -> tuple[list[tuple[str, str, str]], bool]:
    """Return [(item, status, detail)] and whether a fatal problem exists."""
    rows: list[tuple[str, str, str]] = []
    fatal = False

    v = sys.version_info
    if v[:2] >= REQUIRED_PY:
        rows.append(("python", "ok", f"{v.major}.{v.minor}.{v.micro}"))
    else:
        rows.append(("python", "FATAL", f"{v.major}.{v.minor} < 3.10"))
        fatal = True

    if shutil.which("git") and _cmd_ok(["git", "--version"]):
        rows.append(("git", "ok", ""))
    else:
        rows.append(("git", "degraded", "no checkpoints; file-copy fallback"))

    rows.append(("ripgrep", "ok", "") if shutil.which("rg")
                else ("ripgrep", "degraded", "falling back to git grep / python"))

    rows.append(("coverage", "ok", "SBFL enabled") if _module("coverage")
                else ("coverage", "degraded", "no SBFL localization"))

    rows.append(("pytest", "ok", "") if _module("pytest")
                else ("pytest", "degraded", "harness self-tests unavailable"))

    rows.append(("ctags", "ok", "") if shutil.which("ctags")
                else ("ctags", "degraded", "regex symbol extraction"))

    rows.append(("tree-sitter", "ok", "") if _module("tree_sitter")
                else ("tree-sitter", "degraded", "no AST; grep navigation"))

    try:
        probe = Path.cwd() / ".harness_write_probe"
        probe.write_text("x")
        probe.unlink()
        rows.append(("write access", "ok", str(Path.cwd())))
    except OSError as exc:
        rows.append(("write access", "FATAL", str(exc)))
        fatal = True

    return rows, fatal


def main() -> int:
    rows, fatal = check()
    print("\n-- harness readiness " + "-" * 43)
    for item, status, detail in rows:
        mark = {"ok": "ok", "degraded": "degraded", "FATAL": "FATAL"}[status]
        line = f"  {item:<14} {mark}"
        if detail:
            line += f": {detail}" if status != "ok" else f"  {detail}"
        print(line)
    print("-" * 64)
    if fatal:
        print("setup cannot continue: fix the FATAL items above")
        return 1
    print("ready. run:  AI_API_KEY=... make run ISSUE='<issue text>'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
