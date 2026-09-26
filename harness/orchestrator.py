"""Deterministic state machine (SPEC.md 10.2).

Transitions live in a table, never in scattered conditionals, so the loop is
testable in isolation (NFR-5).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import exits
from .budget import Budgets
from .context.assemble import Assembler
from .context.events import EventLog
from .edit.apply import EditFailure
from .localize import sbfl as SBFL
from .localize.oracle import find as find_oracle
from .localize.router import Signals, route as route_of
from .logging_ui import Logger
from .model import bootstrap as boot
from .phases import p0_triage, p1_investigate, p2_scope, p3_implement
from .phases.ctx import PhaseContext
from .records import RootCauseRecord
from .repo import external as EXT
from .repo.search import Search
from .repo.workspace import Workspace
from .verify import parse_results as P
from .verify.baseline import capture
from .verify.toolchain import discover_toolchain


class Orchestrator:
    def __init__(self, cfg, log: Logger) -> None:
        self.cfg = cfg
        self.log = log
        self.run_dir = cfg.work_dir / "run"
        self.events = EventLog(self.run_dir, secrets=[cfg.api_key])
        self.budgets = Budgets.from_config(cfg)
        self.bs = None
        self.caps = None
        self.toolchain = None
        self.startup_s = 0.0

    # -- bootstrap ---------------------------------------------------------
    def bring_up(self) -> None:
        """FR-6..FR-11, NFR-6: printed before any indexing."""
        t0 = time.time()
        self.log.set_phase("P0")
        self.bs = boot.bring_up(self.cfg, self.log, events=self.events,
                                budgets=self.budgets)
        self.workspace = Workspace(self.cfg.repo_path, self.log)
        self.workspace.exclude_harness_dir()
        self.toolchain = discover_toolchain(self.cfg.repo_path, self.cfg)

        repo_info = {"path": str(self.cfg.repo_path),
                     "test_cmd": self.toolchain.test_cmd,
                     "lint_cmd": self.toolchain.lint_cmd}
        try:
            from .repo.lang import describe
            repo_info.update(describe(self.cfg.repo_path))
        except Exception:
            repo_info["language"] = self.toolchain.language

        boot.print_startup(self.bs, self.cfg, self.log, repo_info)
        self.startup_s = time.time() - t0
        self.log.debug(f"startup {self.startup_s:.2f}s")

    # -- run ---------------------------------------------------------------
    def run(self) -> int:
        self.events.append("phase_start", "P0",
                           {"issue_len": len(self.cfg.issue)})
        self.bring_up()

        from .model.probe import probe
        self.caps = probe(self.bs.gateway, self.bs.primary.id,
                          cache_dir=self.cfg.work_dir / "cache")
        if not self.caps.tool_calling:
            self.log.degraded("text_protocol", "no tool calling detected")
        if not self.caps.strict_json:
            self.log.degraded("loose_json", "fenced-block protocol in use")

        ctx = self._context()

        # -- P0 triage -----------------------------------------------------
        self.log.phase("P0")
        files = set(ctx.search.files())
        issue = p0_triage.triage(self.cfg.issue, files,
                                 forced_type=self.cfg.task_type)
        eff_type, conservative = p0_triage.effective_type(issue.task_type)
        if conservative:
            self.cfg.conservative = True
        self.log.line(f"task type {issue.task_type}"
                      + (f" -> {eff_type} (conservative)" if conservative else ""))
        self.log.line(f"vagueness {issue.vagueness} -> "
                      f"{issue.min_hypotheses} hypotheses minimum")
        self.log.cont(issue.anchors.render())
        self.events.append("phase_end", "P0", issue.to_json(),
                           summary=issue.title[:80])
        self._write("issue.json", issue.to_json())

        # -- baseline + localization (all deterministic) -------------------
        baseline = capture(self.cfg.repo_path, self.toolchain, self.cfg,
                           self.log, self.run_dir)
        self._write("baseline.json", baseline.to_json())

        ctx.external = EXT.probe_all(self.cfg.repo_path, self.toolchain,
                                     ctx.search)
        oracle = find_oracle(baseline, issue)
        relevant = ([oracle.test_id] if oracle else
                    [t for t, s in baseline.tests.items() if s in P.FAILING])
        sbfl = SBFL.localize(baseline, relevant, self.cfg.repo_path)

        signals = Signals(
            sbfl=sbfl.top_files(6) if sbfl.ok else [],
            lexical=issue.anchors.files[:6],
            structural=[f for f, _l, _fn in issue.anchors.frames][:6],
            historical=[],
        )
        route = route_of(signals, oracle, forced=self.cfg.route)

        # -- P1 investigate ------------------------------------------------
        self.events.append("phase_start", "P1", {"route": route.name})
        inv = p1_investigate.Investigation(ctx)
        root_cause: RootCauseRecord = inv.run(issue, baseline, sbfl, oracle,
                                              route, ctx.external)
        p1_investigate.apply_gate(root_cause, files, self.cfg, self.log)
        self.log.line(f"root cause: {root_cause.statement}")
        self.log.cont(f"class={root_cause.classification}  "
                      f"confidence={root_cause.confidence}  "
                      f"files={', '.join(f.path for f in root_cause.files[:2])}")
        self.events.append("phase_end", "P1", root_cause.to_json(),
                           summary=root_cause.statement[:80])
        self._write("rootcause.json", root_cause.to_json())

        # -- P2 scope ------------------------------------------------------
        self.events.append("phase_start", "P2", {})
        plan = p2_scope.scope(ctx, issue, root_cause)
        self.events.append("phase_end", "P2", plan.to_json(),
                           summary=plan.fix_description[:80])
        self._write("scope.json", plan.to_json())

        if self.cfg.dry_run:
            self.log.raw("")
            self.log.raw("dry run: P0-P2 only, nothing was written")
            return exits.NO_FIX

        # -- P3 implement --------------------------------------------------
        self.events.append("phase_start", "P3", {})
        impl = p3_implement.Implementation(ctx)
        feedback = ""
        applied = None
        for attempt in range(3):
            try:
                applied, flags = impl.run(issue, root_cause, plan, feedback)
                break
            except EditFailure as exc:
                self.log.warn(f"edit rejected: {exc}")
                self.events.append("degradation", "P3",
                                   {"stage": exc.stage, "detail": exc.detail})
                feedback = exc.feedback()
        if applied is None:
            self.log.warn("no appliable edit after 3 attempts")
            self.workspace.revert_all()
            return exits.NO_FIX
        self.events.append("phase_end", "P3",
                           {"files": [a.path for a in applied]})
        self._write("diff.patch", {"diff": self.workspace.diff()})

        self.log.phase("P4")
        self.log.line("verification lands in WP6")
        return exits.NO_FIX

    # -- helpers -----------------------------------------------------------
    def _context(self) -> PhaseContext:
        return PhaseContext(
            cfg=self.cfg, log=self.log, router=self.bs.router,
            repo=self.cfg.repo_path, search=Search(self.cfg.repo_path, self.log),
            workspace=self.workspace, toolchain=self.toolchain,
            events=self.events, budgets=self.budgets, caps=self.caps,
            assembler=Assembler(self.cfg, self.log, self.bs.primary.ctx),
        )

    def _write(self, name: str, payload: dict) -> None:
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            (self.run_dir / name).write_text(
                json.dumps(payload, indent=2, default=str), encoding="utf-8")
        except OSError:
            pass
