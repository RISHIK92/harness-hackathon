"""Post-apply validation: syntax, scope, size (FR-24, FR-26, SPEC.md 8.3)."""
from __future__ import annotations

import ast
import shlex
from pathlib import Path

from ..verify.runner import run
from .apply import Applied, EditFailure

SIZE_FACTOR = 1.3


def syntax_check(repo: Path, path: str, toolchain=None) -> None:
    """Raise EditFailure if the edit left the file unparseable."""
    target = Path(repo) / path
    suffix = target.suffix.lower()
    try:
        source = target.read_text("utf-8", errors="replace")
    except OSError as exc:
        raise EditFailure("syntax", f"cannot read back: {exc}", path=path)

    if suffix in (".py", ".pyi"):
        try:
            ast.parse(source)
        except SyntaxError as exc:
            raise EditFailure(
                "syntax", f"line {exc.lineno}: {exc.msg}", path=path)
        return

    checker = {
        ".go": "gofmt -e",
        ".rs": None,
        ".js": "node --check",
        ".mjs": "node --check",
        ".ts": None,
        ".json": None,
    }.get(suffix)
    if checker:
        # The checker is ours; the file name is the model's (it can create
        # files), so it is quoted and the deny list, which reads commands,
        # has nothing to add.
        r = run(f"{checker} {shlex.quote(str(target))}", repo, timeout=30,
                check_deny=False)
        if not r.ok:
            raise EditFailure("syntax", r.output[-400:] or "check failed",
                              path=path)
    elif suffix == ".json":
        import json
        try:
            json.loads(source)
        except json.JSONDecodeError as exc:
            raise EditFailure("syntax", str(exc), path=path)


def scope_check(applied: list[Applied], plan) -> None:
    """A write outside the plan is reverted, not warned about."""
    allowed, forbidden = plan.allowed, plan.forbidden
    for a in applied:
        # A rename touches two paths, and the destination is the one that
        # survives -- checking only the source would let a file be moved
        # anywhere at all.
        dest = getattr(a, "dest", "")
        if dest:
            if dest in forbidden:
                raise EditFailure("scope", f"{dest} is on the must-not-change "
                                           f"list.", path=dest)
            if allowed and dest not in allowed:
                raise EditFailure(
                    "scope", f"{dest} is not in the plan "
                             f"({', '.join(sorted(allowed)[:3])}).", path=dest)
        if a.path in forbidden:
            raise EditFailure(
                "scope", f"{a.path} is on the must-not-change list.",
                path=a.path)
        if allowed and a.path not in allowed:
            raise EditFailure(
                "scope",
                f"{a.path} is not in the plan "
                f"({', '.join(sorted(allowed)[:3])}).", path=a.path)


def size_check(applied: list[Applied], plan, conservative: bool = False) -> None:
    """FR-26: more than 30% over the estimate triggers a re-evaluation."""
    total = sum(a.added + a.removed for a in applied)
    cap = int(plan.estimated_lines_changed * SIZE_FACTOR)
    if conservative:
        cap = min(cap, 15)
    if total > cap:
        raise EditFailure(
            "size",
            f"the change is {total} lines against an estimate of "
            f"{plan.estimated_lines_changed} (cap {cap}).")


def validate(repo: Path, applied: list[Applied], plan, cfg,
             toolchain=None) -> list[str]:
    """Run every gate in order; return hygiene flags (advisory)."""
    from . import hygiene
    from ..repo.snippets import style_profile

    scope_check(applied, plan)
    for a in applied:
        syntax_check(repo, a.path, toolchain)
    size_check(applied, plan, cfg.conservative)

    flags: list[str] = []
    for a in applied:
        style = style_profile(Path(repo) / a.path)
        flags += hygiene.scan(a.before, a.after, a.path, style)
    return sorted(set(flags))
