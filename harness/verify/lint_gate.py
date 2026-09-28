"""FR-27: lint scoped to changed files, diffed against the baseline.

Running the linter across a repository that carries pre-existing debt
produces a gate that can never go green -- the harness then burns every cycle
without running a single test.  Two rules prevent that:

  1. only the repository's own configured linter is ever run;
  2. only diagnostics absent from the baseline block.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from ..repo.paths import norm
from .runner import run

# A linter that is configured but not installed must DEGRADE, never pass
# vacuously: a gate that silently always succeeds is worse than no gate.
NOT_INSTALLED = re.compile(
    r"(No module named|command not found|is not recognized|"
    r"ModuleNotFoundError|not found: )", re.I)

# Known linters render diagnostics in several shapes. Ask for the stable,
# machine-readable one where the tool supports it -- the same reason the test
# runner is asked for junit-xml.
CONCISE_FLAG = {
    "ruff": "--output-format=concise",
    "mypy": "--no-pretty --no-color-output",
    "eslint": "--format=compact",
}

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# file:line:col: CODE message   (concise form, and ruff's `--> file:line:col`)
DIAG = re.compile(r"^(?:\s*-->\s*)?(?P<file>[^\s:][^:]*):(?P<line>\d+)"
                  r"(?::(?P<col>\d+))?:?\s*(?P<rest>.*)$", re.M)


def concise(cmd: str) -> str:
    """Append the tool's machine-readable output flag when we recognise it."""
    for tool, flag in CONCISE_FLAG.items():
        if re.search(rf"\b{tool}\b", cmd) and flag.split("=")[0] not in cmd:
            return f"{cmd} {flag}"
    return cmd


@dataclass
class LintResult:
    new: list = field(default_factory=list)
    pre_existing: list = field(default_factory=list)
    ran: bool = False
    reason: str = ""

    @property
    def blocks(self) -> bool:
        return bool(self.new)

    def render(self) -> str:
        if not self.ran:
            return f"lint: {self.reason}"
        if self.new:
            return ("lint: " + str(len(self.new)) + " new diagnostic(s)\n"
                    + "\n".join("  " + d for d in self.new[:10]))
        extra = (f" ({len(self.pre_existing)} pre-existing, untouched)"
                 if self.pre_existing else "")
        return f"lint: clean - no new diagnostics on changed files{extra}"


def parse_diagnostics(text: str) -> set:
    out = set()
    for m in DIAG.finditer(ANSI.sub("", text or "")):
        path = norm(m.group("file"))
        if not path or " " in path or not _looks_like_path(path):
            continue
        code = m.group("rest").strip().split(" ")[0].rstrip(":") or "?"
        out.add(f"{path}:{m.group('line')}:{code}")
    return out


def _looks_like_path(p: str) -> bool:
    return "/" in p or "." in p


def capture_baseline(repo: Path, toolchain, log) -> set:
    """Whole-repo diagnostics BEFORE any edit. Reference, not a gate."""
    if not toolchain or not toolchain.lint_cmd:
        return set()
    # The repository's (or the operator's) own lint command, unmodified.
    r = run(concise(toolchain.lint_cmd), repo, timeout=120,
            check_deny=False)
    if NOT_INSTALLED.search(r.output):
        log.degraded("no_linter", "configured linter is not installed")
        return set()
    diags = parse_diagnostics(r.output)
    if diags:
        log.note("lint_debt", f"{len(diags)} pre-existing diagnostic(s); "
                              "these will never block")
    return diags


def gate(repo: Path, toolchain, changed: list, baseline_lint: set,
         log) -> LintResult:
    """FR-27: new diagnostics, on files we touched, block the test run."""
    if not toolchain or not toolchain.lint_cmd:
        return LintResult(ran=False,
                          reason="the repository configures no linter")
    from ..repo.search import is_source
    lintable = [p for p in changed if is_source(p)]
    if not lintable:
        return LintResult(ran=False, reason="no lintable files changed")

    # The linter is the repository's; the paths are files the model changed
    # or created. Double quotes still expand `$(...)`, so each path is
    # shell-quoted instead.
    targets = " ".join(shlex.quote(p) for p in lintable)
    r = run(f"{concise(toolchain.lint_cmd)} {targets}", repo, timeout=120,
            check_deny=False)
    if NOT_INSTALLED.search(r.output):
        return LintResult(ran=False,
                          reason="the configured linter is not installed")
    current = parse_diagnostics(r.output)

    new = sorted(current - baseline_lint)
    pre = sorted(current & baseline_lint)
    result = LintResult(new=new, pre_existing=pre, ran=True)
    head = result.render().splitlines()[0].replace("lint: ", "", 1)
    (log.fail if result.blocks else log.computed)(
        "lint", head, plain=result.render().splitlines()[0])
    for d in new[:5]:
        log.cont(d)
    return result
