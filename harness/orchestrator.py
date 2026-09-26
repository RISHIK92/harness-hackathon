"""Deterministic state machine (SPEC.md 10.2).

Transitions live in a table, never in scattered conditionals, so the loop is
testable in isolation (NFR-5).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import exits
from .budget import BudgetExceeded, Budgets
from .context.assemble import Assembler
from .context.events import EventLog
from .edit.apply import EditFailure
from .localize import sbfl as SBFL
from .localize.oracle import find as find_oracle
from .localize.router import Signals, route as route_of
from .logging_ui import Logger
from .model import bootstrap as boot
from .phases import (p0_triage, p1_investigate, p2_scope, p3_implement,
                     p4_verify, p5_confidence)
from .phases.p5_confidence import REMEDY_PHASE, Action
from .recovery import alternatives, stuck, taxonomy
from . import report as REPORT
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
        plan = p2_scope.scope(ctx, issue, root_cause, route.candidates)
        self.events.append("phase_end", "P2", plan.to_json(),
                           summary=plan.fix_description[:80])
        self._write("scope.json", plan.to_json())

        if self.cfg.dry_run:
            self.log.raw("")
            self.log.raw("dry run: P0-P2 only, nothing was written")
            return exits.NO_FIX

        # -- the fix-and-verify loop (FR-33) -------------------------------
        verifier = p4_verify.Verifier(ctx, baseline)
        impl = p3_implement.Implementation(ctx)
        watch = stuck.StuckState()
        attempts: list[dict] = []
        tried_hypotheses: set = set()
        remedy_counts: dict = {}
        feedback = ""
        summary = REPORT.RunSummary()
        vres = None
        conf = None

        for cycle in range(1, self.cfg.max_cycles + 1):
            summary.cycles = cycle
            self.log.raw("")
            self.log.line(f"cycle {cycle} of {self.cfg.max_cycles}",
                          phase="P3")

            # P3
            try:
                applied, flags = impl.run(issue, root_cause, plan, feedback)
            except EditFailure as exc:
                fail = taxonomy.classify_edit_failure(exc)
                self.log.warn(f"{fail.render()}  -> {fail.first_move}")
                self.events.append("degradation", "P3",
                                   {"kind": fail.kind, "detail": fail.detail})
                watch.failure(fail.render())
                feedback = exc.feedback()
                self.workspace.revert_all()
                if self._stuck(watch, "P3"):
                    break
                continue
            except BudgetExceeded:
                self.cfg.conservative = True
                self.log.degraded("budget", "conservative mode; going to P5")
                break

            changed = self.workspace.changed_files()
            watch.diff(self.workspace.diff())
            watch.observe(changed)

            # P4
            try:
                vres = verifier.run(plan, root_cause, changed, oracle,
                                    final=True)
            except BudgetExceeded:
                self.cfg.conservative = True
                self.log.degraded("budget", "verification cut short")
                break

            # P5
            self.log.phase("P5")
            conf = p5_confidence.score(root_cause, plan, vres,
                                       self.workspace, self.cfg)
            self.log.line(conf.render())
            action = p5_confidence.decide(conf, cycle, self.cfg.max_cycles)
            self.log.line(f"decision: {action.value}"
                          + (f"  (blocking: {', '.join(conf.blocking)})"
                             if conf.blocking else ""))

            attempts.append({
                "cycle": cycle,
                "checkpoint": self.workspace.checkpoint(f"cycle-{cycle}"),
                "new_failures": len(vres.blocking),
                "judge_ok": bool(vres.judge.addresses),
                "hygiene": len(flags),
                "diff_lines": sum(a.added + a.removed for a in applied),
                "confidence": conf,
                "verify": vres,
            })
            self.events.append("checkpoint", "P5",
                               {"cycle": cycle, "action": action.value,
                                "confidence": conf.to_json()})

            if action is Action.SUBMIT:
                summary.status = "SUCCESS"
                summary.exit_code = exits.SUCCESS
                break

            # -- remedy routing (FR-35): never a silent submission ----------
            phase = REMEDY_PHASE.get(action, "P3")
            key = (phase, action.value)
            remedy_counts[key] = remedy_counts.get(key, 0) + 1
            if remedy_counts[key] > 2:
                phase = stuck.escalate(phase)
                self.log.line(f"anti-thrash: escalating to {phase}")

            failure = taxonomy.classify_tests(
                vres.full or vres.scoped, lint_blocked=vres.lint.blocks)
            watch.failure(failure.render())
            feedback = self._feedback_for(action, conf, vres, failure)

            if self._stuck(watch, phase):
                break

            self.workspace.revert_all()

            if phase == "P1":
                nxt = alternatives.next_alternative(root_cause,
                                                    tried_hypotheses)
                if nxt:
                    tried_hypotheses.add(nxt.id)
                    self.log.line("recovery: re-entering P1 with the "
                                  f"next alternative - {alternatives.describe(nxt)}")
                    root_cause.statement = nxt.statement
                    root_cause.confidence = "medium"
                else:
                    self.log.line("no untried alternative remains -> "
                                  "conservative mode")
                    self.cfg.conservative = True
                plan = p2_scope.scope(ctx, issue, root_cause,
                                      route.candidates)
            elif phase == "P2":
                plan.estimated_lines_changed = max(
                    2, plan.estimated_lines_changed // 2)
                self.log.line(f"re-scoped: estimate now "
                              f"{plan.estimated_lines_changed} lines")

        # -- exhausted: restore the best attempt (FR-33) -------------------
        if summary.status != "SUCCESS":
            best = self._best(attempts)
            if best:
                self.workspace.restore(best["checkpoint"])
                conf = best["confidence"]
                vres = best["verify"]
                summary.cycles = best["cycle"]
                if conf and all(getattr(conf, c) for c in conf.HARD):
                    summary.status = "PARTIAL"
                    summary.exit_code = exits.PARTIAL
                    summary.reason = ("hard gates green, soft condition(s) "
                                      "failed: " + ", ".join(conf.blocking))
                else:
                    summary.status = "PARTIAL"
                    summary.exit_code = exits.PARTIAL
                    summary.reason = ("best attempt restored; blocking: "
                                      + ", ".join(conf.blocking if conf
                                                  else ["unknown"]))
                self.log.line(f"restored the best attempt (cycle "
                              f"{best['cycle']}): {best['new_failures']} new "
                              f"failure(s), {best['diff_lines']} lines",
                              phase="P5")
            else:
                self.workspace.revert_all()
                summary.status = "NO_FIX"
                summary.exit_code = exits.NO_FIX
                summary.reason = "no appliable, verifiable change was produced"

        summary.attempts = len(attempts)
        self._write("confidence.json", conf.to_json() if conf else {})
        if vres:
            self._write("verification.json", vres.to_json())
        self._write("diff.patch", {"diff": self.workspace.diff()})

        text = REPORT.build(self.cfg, self.log, summary, issue, root_cause,
                            plan, vres, conf, self.workspace, self.budgets,
                            route, ctx.notes)
        REPORT.write(self.run_dir, text)
        REPORT.render_stdout(self.log, self.cfg, summary, root_cause, plan,
                             vres, conf, self.workspace, self.budgets)
        self.log.raw("")
        self.log.raw("=== DIFF ===")
        self.log.raw(self.workspace.diff() or "(no changes)")
        return summary.exit_code

    # -- loop helpers ------------------------------------------------------
    def _stuck(self, watch, phase: str) -> bool:
        label = watch.check()
        if not label:
            return False
        watch.fire(label)
        self.log.warn(f"stuck: {label}")
        self.events.append("degradation", phase,
                           {"kind": "stuck", "detail": label})
        return len(watch.triggers) >= 2

    def _feedback_for(self, action, conf, vres, failure) -> str:
        if action is Action.REVERT_AND_REIMPLEMENT:
            return ("The previous attempt modified files outside the plan. "
                    "Change only the planned file.")
        if action is Action.RESCOPE:
            return ("The previous change was too large for its scope. "
                    "Produce a smaller change.")
        if action is Action.REIMPLEMENT_TARGETED:
            detail = ", ".join(vres.blocking[:3]) if vres else failure.detail
            return (f"The previous attempt broke: {detail}. "
                    f"{failure.first_move}.")
        return failure.detail or "The previous attempt did not resolve the issue."

    @staticmethod
    def _best(attempts: list) -> dict | None:
        """(new_failures asc, judge desc, hygiene asc, diff asc)."""
        if not attempts:
            return None
        return sorted(attempts, key=lambda a: (a["new_failures"],
                                               0 if a["judge_ok"] else 1,
                                               a["hygiene"],
                                               a["diff_lines"]))[0]

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
