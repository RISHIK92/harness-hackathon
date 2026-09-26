"""Phase 4: verification (FR-27..FR-33, SPEC.md 9, 26.2).

Gate order is cheapest-first.  The full suite runs ONCE as the pre-submission
gate rather than on every cycle: five cycles against a four-minute suite
would consume the whole wall-clock budget.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..verify import classify as CL
from ..verify import lint_gate as LG
from ..verify import parse_results as P
from ..verify import scope_tests
from ..verify.judges import DiffVerdict, best_practices, diff_sanity
from ..verify.runner import run

FULL_SUITE_MAX = 2


@dataclass
class VerifyResult:
    lint: LG.LintResult = field(default_factory=LG.LintResult)
    scoped: CL.Classification = None
    full: CL.Classification = None
    oracle_passes: bool | None = None
    judge: DiffVerdict = field(default_factory=DiffVerdict)
    practices: object = None
    ran_full: bool = False
    stage: str = ""              # the gate that stopped us, if any

    @property
    def blocking(self) -> list:
        out = []
        for cls in (self.scoped, self.full):
            if cls:
                out += cls.blocking
        return sorted(set(out))

    @property
    def clean(self) -> bool:
        return (not self.lint.blocks and not self.blocking
                and self.oracle_passes is not False)

    def to_json(self) -> dict:
        return {
            "lint": {"new": self.lint.new,
                     "pre_existing": len(self.lint.pre_existing),
                     "ran": self.lint.ran},
            "scoped": self.scoped.to_json() if self.scoped else None,
            "full": self.full.to_json() if self.full else None,
            "oracle_passes": self.oracle_passes,
            "judge": {"addresses": self.judge.addresses,
                      "conclusive": self.judge.conclusive,
                      "reason": self.judge.reason},
            "practices": (self.practices.issues if self.practices else []),
            "ran_full": self.ran_full,
            "stage": self.stage,
        }


class Verifier:
    def __init__(self, ctx, baseline) -> None:
        self.ctx = ctx
        self.baseline = baseline
        self.full_runs = 0

    # -- gates -------------------------------------------------------------
    def run(self, plan, root_cause, changed: list, oracle=None,
            final: bool = False) -> VerifyResult:
        c = self.ctx
        c.log.phase("P4")
        res = VerifyResult()

        # G1 lint, scoped to changed files, diffed against the baseline
        res.lint = LG.gate(c.repo, c.toolchain, changed, self.baseline.lint,
                           c.log)
        if res.lint.blocks:
            res.stage = "lint"
            return res

        # oracle: the single test the issue is about
        if oracle:
            res.oracle_passes = self._run_one(oracle.test_id)
            c.log.line(f"oracle {oracle.test_id.split('::')[-1]}: "
                       f"{'PASS' if res.oracle_passes else 'FAIL'}")

        # G2 scoped tests
        symbols = [f.symbol for f in plan.files_to_change if f.symbol]
        scope = scope_tests.select(c.repo, changed, symbols, c.toolchain,
                                   c.search)
        if scope:
            c.log.line(f"scoped tests ({scope.method}): {scope.selector[:70]}")
            res.scoped = self._run_and_classify(scope.selector)
            if res.scoped and res.scoped.blocking:
                res.stage = "scoped"
                self._report(res.scoped)
                return res
        else:
            c.log.line(f"scoped tests: {scope.method}")

        # G3 full suite -- once, as the pre-submission gate
        if final or not scope:
            if self.full_runs >= FULL_SUITE_MAX:
                c.log.note("full_suite_cap",
                           f"already run {self.full_runs} times")
            else:
                c.log.line("full suite (pre-submission gate)")
                res.full = self._run_and_classify("")
                res.ran_full = True
                self.full_runs += 1
                self._report(res.full)
                if res.full and res.full.blocking:
                    res.stage = "full"
                    return res

        # G4 diff sanity, cheapest model
        diff = c.workspace.diff()
        res.judge = diff_sanity(c, root_cause, plan, diff)
        c.log.line(res.judge.render())
        if not res.judge.addresses and res.judge.conclusive:
            res.stage = "judge"
            return res

        # G5 best practices, flag only
        res.practices = best_practices(c, self._modified_functions(changed))
        c.log.line(res.practices.render())
        return res

    # -- helpers -----------------------------------------------------------
    def _test_cmd(self, selector: str) -> str:
        cmd = self.ctx.toolchain.test_cmd
        return f"{cmd} {selector}".strip() if selector else cmd

    def _run_and_classify(self, selector: str):
        c = self.ctx
        junit = c.cfg.work_dir / "run" / "current-junit.xml"
        cmd = self._test_cmd(selector)
        if c.toolchain.junit_flag:
            cmd = f"{cmd} {c.toolchain.junit_flag.format(path=junit)}"
        result = run(cmd, c.repo, timeout=300, check_deny=False)
        parsed = P.parse(result, c.toolchain, junit_path=junit)
        c.events.append("test_run", "P4",
                        {"cmd": cmd, "counts": parsed.counts(),
                         "failing": parsed.failing[:20]},
                        summary=f"{len(parsed.failing)} failing")
        cls = CL.classify(self.baseline.tests, parsed)
        if cls.new or cls.unknown:
            cls = CL.rerun_for_flakes(cls, self._run_one_fails, c.log)
        return cls

    def _run_one(self, test_id: str) -> bool:
        return not self._run_one_fails(test_id)

    def _run_one_fails(self, test_id: str) -> bool:
        """True when the test still fails in isolation."""
        c = self.ctx
        selector = _selector_for(test_id, c.toolchain)
        result = run(self._test_cmd(selector), c.repo, timeout=120,
                     check_deny=False)
        return result.exit_code != 0

    def _report(self, cls) -> None:
        if not cls:
            return
        c = self.ctx
        bits = []
        if cls.new:
            bits.append(f"{len(cls.new)} new failure(s)")
        if cls.pre_existing:
            bits.append(f"{len(cls.pre_existing)} pre-existing")
        if cls.fixed:
            bits.append(f"{len(cls.fixed)} now passing")
        if cls.flaky:
            bits.append(f"{len(cls.flaky)} flaky")
        c.log.line("tests: " + (", ".join(bits) or "no changes"))
        for tid in cls.new[:4]:
            c.log.cont(f"NEW  {tid}")
        for tid in cls.pre_existing[:3]:
            c.log.cont(f"pre-existing (not ours) {tid}")

    def _modified_functions(self, changed: list) -> list:
        from ..repo.snippets import symbols
        out = []
        for path in changed[:3]:
            full = Path(self.ctx.repo) / path
            try:
                text = full.read_text("utf-8", errors="replace")
            except OSError:
                continue
            lines = text.splitlines(keepends=True)
            for sym in symbols(full):
                if sym.kind == "class":
                    continue
                body = "".join(lines[sym.start - 1:sym.end])
                out.append((path, sym.name, body))
        return out[:4]


def _selector_for(test_id: str, toolchain) -> str:
    if toolchain.language == "python":
        module, _, name = test_id.partition("::")
        path = module.replace(".", "/") + ".py" if "/" not in module else module
        return f'"{path}::{name}"' if name else f'"{path}"'
    if toolchain.language == "go":
        _, _, name = test_id.partition("::")
        return f"-run '^{name}$' ./..."
    _, _, name = test_id.partition("::")
    return f'-t "{name}"'
