"""Spectrum-based fault localization (SPEC.md 21.1).

The baseline suite already runs before the first edit, so instrumenting it
with `coverage` makes this free.  Lines executed by failing tests and not by
passing ones rank highest -- a causal signal, where grep and PageRank are
lexical ones.  Zero model calls (NFR-1).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from math import sqrt
from pathlib import Path

from ..repo.snippets import symbols


@dataclass
class Suspect:
    path: str
    line: int
    score: float
    symbol: str = ""

    def render(self) -> str:
        where = f"{self.path}:{self.line}"
        if self.symbol:
            where += f" ({self.symbol})"
        return f"{where}  suspiciousness {self.score:.3f}"


@dataclass
class SBFLResult:
    lines: list = field(default_factory=list)      # Suspect, best first
    files: list = field(default_factory=list)      # (path, score)
    ok: bool = False
    reason: str = ""

    def top_files(self, n: int = 8) -> list:
        return [p for p, _s in self.files[:n]]

    def render(self, n: int = 5) -> str:
        if not self.ok:
            return f"coverage localization unavailable ({self.reason})"
        return "\n".join("  " + s.render() for s in self.lines[:n])


def match_context(ctx: str, test_id: str) -> bool:
    """Reconcile coverage contexts with junit ids.

    coverage emits `module.test_func`; junit emits `package.module::test_func`.
    """
    ctx = ctx.split("|")[0].strip()
    if not ctx:
        return False
    if "." not in ctx:
        return ctx == test_id.split("::")[-1]
    module, _, func = ctx.rpartition(".")
    tid_mod, _, tid_func = test_id.partition("::")
    if func != tid_func:
        return False
    tid_mod = tid_mod.replace("/", ".").removesuffix(".py")
    return tid_mod.endswith(module) or module.endswith(tid_mod)


def ochiai(coverage: dict, failing: set[str], passing: set[str],
           repo: Path | None = None) -> SBFLResult:
    """Suspiciousness per line: ef / sqrt(total_failed * (ef + ep))."""
    res = SBFLResult()
    if not coverage:
        res.reason = "no coverage data"
        return res
    if not failing:
        res.reason = "baseline has no failing test"
        return res

    total_failed = len(failing)
    suspects: list[Suspect] = []
    for path, lines in coverage.items():
        for line, contexts in lines.items():
            ef = ep = 0
            for ctx in contexts:
                if any(match_context(ctx, t) for t in failing):
                    ef += 1
                elif any(match_context(ctx, t) for t in passing):
                    ep += 1
            if ef:
                score = ef / sqrt(total_failed * (ef + ep))
                suspects.append(Suspect(path, int(line), round(score, 4)))

    if not suspects:
        res.reason = "no line executed by a failing test"
        return res

    suspects.sort(key=lambda s: (-s.score, s.path, s.line))
    _attach_symbols(suspects, repo)
    res.lines = suspects

    per_file: dict[str, float] = {}
    for s in suspects:
        per_file.setdefault(s.path, 0.0)
        per_file[s.path] += s.score
    res.files = sorted(per_file.items(), key=lambda kv: (-kv[1], kv[0]))
    res.ok = True
    return res


def _attach_symbols(suspects: list, repo: Path | None = None) -> None:
    """Coverage paths are repo-relative; symbol extraction needs the root."""
    root = Path(repo) if repo else Path(".")
    cache: dict[str, list] = {}
    for s in suspects[:200]:
        if s.path not in cache:
            try:
                cache[s.path] = symbols(root / s.path)
            except Exception:
                cache[s.path] = []
        for sym in cache[s.path]:
            if sym.start <= s.line <= sym.end and sym.kind != "class":
                s.symbol = sym.qualname
                break


TEST_PATH = re.compile(r"(^|/)(tests?|spec)/|_test\.|test_|\.test\.|_spec\.")


def is_test_path(path: str) -> bool:
    return bool(TEST_PATH.search(path))


def localize(baseline, failing_tests: list[str],
             repo: Path | None = None) -> SBFLResult:
    """Entry point: rank suspects from the baseline's coverage contexts.

    `failing_tests` should be the ISSUE-RELEVANT failures.  Feeding it every
    red test lets unrelated pre-existing failures dominate the ranking.
    """
    if not getattr(baseline, "coverage_ok", False):
        out = SBFLResult()
        out.reason = "baseline was not instrumented"
        return out
    failing = set(failing_tests)
    passing = {t for t, s in baseline.tests.items() if s == "pass"}
    res = ochiai(baseline.coverage, failing, passing, repo)
    # Source lines are what we can fix; test files are evidence, not targets.
    res.lines = [s for s in res.lines if not is_test_path(s.path)]
    res.files = [(p, s) for p, s in res.files if not is_test_path(p)]
    res.ok = res.ok and bool(res.lines)
    if not res.lines and not res.reason:
        res.reason = "only test files were implicated"
    return res
