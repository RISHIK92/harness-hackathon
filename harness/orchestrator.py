"""Deterministic state machine (SPEC.md 10.2).

Transitions live in a table, never in scattered conditionals, so the loop is
testable in isolation (NFR-5).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
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
from .verify import repro as REPRO
from .verify.baseline import capture
from .verify.toolchain import discover_toolchain


def _lines_in(detail: str) -> int:
    """The actual size reported by the size gate's own message."""
    m = re.search(r"is (\d+) lines", detail or "")
    return int(m.group(1)) if m else 0


class Orchestrator:
    def __init__(self, cfg, log: Logger, gate=None, clarify=None) -> None:
        self.cfg = cfg
        self.log = log
        self.gate = gate
        self.clarify = clarify
        self.run_dir = cfg.work_dir / "run"
        self.events = EventLog(self.run_dir, secrets=[cfg.api_key])
        self.budgets = Budgets.from_config(cfg)
        self.bs = None
        self.caps = None
        self.log.bind_counters(self._counters)
        self.toolchain = None
        self.startup_s = 0.0

    def _counters(self) -> str:
        """The trailing `· 14.2k tokens · 0:12` on the live status line."""
        from .ui import human_time, human_tokens
        t = self.budgets.tokens
        bits = [human_time(self.budgets.clock.elapsed)]
        if t.used:
            bits.append(f"{human_tokens(t.used)} tokens")
        if t.used_cached:
            bits.append(f"{t.cache_ratio()*100:.0f}% cached")
        return "   " + f" {self.log.g.DOT} ".join(bits)

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
        try:
            return self._run()
        finally:
            # However the run ends -- budget, exception, ctrl-c -- the
            # container goes with it. A leaked one holds a mount open.
            try:
                self._stop_container()
            except (OSError, RuntimeError, AttributeError) as exc:
                self.log.debug(f"could not stop the container: {exc}")

    def _run(self) -> int:
        self.events.append("phase_start", "P0",
                           {"issue_len": len(self.cfg.issue)})
        self.log.working("starting up")
        self.bring_up()
        self.cfg._primary = self.bs.primary

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

        # Budgets are defaults set against small fixtures. A real repository
        # is orders of magnitude larger and every step scales with it. An
        # operator who named a budget meant it, so theirs is left alone.
        import os
        if not (os.environ.get("HARNESS_TIME_BUDGET")
                or os.environ.get("HARNESS_TOKEN_BUDGET")):
            self.budgets.scale_to_repo(len(files), self.log)
            self.cfg.time_budget = self.budgets.clock.limit_s
            self.cfg.token_budget = self.budgets.tokens.total
        issue = p0_triage.triage(self.cfg.issue, files,
                                 forced_type=self.cfg.task_type)
        eff_type, conservative = p0_triage.effective_type(issue.task_type)
        if conservative:
            self.cfg.conservative = True
        self.log.computed(
            "task type",
            issue.task_type
            + (f" -> {eff_type} (conservative)" if conservative else ""),
            plain=f"task type {issue.task_type}"
                  + (f" -> {eff_type} (conservative)" if conservative else ""))
        self.log.computed(
            "vagueness", f"{issue.vagueness}  -> {issue.min_hypotheses} "
                         f"hypotheses minimum",
            plain=f"vagueness {issue.vagueness} -> "
                  f"{issue.min_hypotheses} hypotheses minimum")
        self.log.cont(issue.anchors.render())
        # Too vague to act on, and somebody is here to ask.
        issue = self._clarify_if_vague(issue, files)

        self.events.append("phase_end", "P0", issue.to_json(),
                           summary=issue.title[:80])
        self._write("issue.json", issue.to_json())

        # -- an environment that can actually build and test this repo ------
        self.provision = self._provision()
        self.container = self.provision.container

        # -- dependencies --------------------------------------------------
        # Before the baseline, because a suite that cannot start is not a
        # baseline -- it is exit 127 misread as a red suite.
        self.deps = self._install_dependencies()

        # -- baseline + localization (all deterministic) -------------------
        self.log.working("running the suite to establish a baseline")
        baseline = capture(self.cfg.repo_path, self.toolchain, self.cfg,
                           self.log, self.run_dir)

        # The suite is configured but produced nothing. That is not a
        # repository without tests -- it is a repository whose tests cannot
        # run HERE, usually a native module built against a different
        # runtime. "node is installed" said the host was fine; the baseline
        # says otherwise, and the baseline is the evidence. Escalate the
        # ladder once and try again in the runtime the project declares.
        baseline = self._retry_baseline_in_container(baseline)
        self._write("baseline.json", baseline.to_json())

        ctx.external = EXT.probe_all(self.cfg.repo_path, self.toolchain,
                                     ctx.search)
        oracle = find_oracle(baseline, issue)
        relevant = ([oracle.test_id] if oracle else
                    [t for t, s in baseline.tests.items() if s in P.FAILING])
        sbfl = SBFL.localize(baseline, relevant, self.cfg.repo_path)

        # What the failing tests import. Coverage-based localization is
        # Python-only here, and the lexical signal greps the issue's words --
        # which are a reporter's words ("bucket"), not the code's
        # (`projectBreakdown`). Without this, every signal on a JavaScript
        # repository is empty, and an empty signal set produces an empty
        # plan and a run that changes nothing.
        from .localize import from_tests as FT
        by_test = FT.candidates(self.cfg.repo_path, ctx.search, relevant)
        if by_test:
            self.log.computed("from failing tests", ", ".join(by_test[:3]),
                              plain=f"from failing tests: {by_test[:3]}")

        lexical = self._lexical_signal(ctx, issue)
        signals = Signals(
            sbfl=sbfl.top_files(6) if sbfl.ok else [],
            lexical=lexical or by_test,
            structural=([f for f, _l, _fn in issue.anchors.frames][:6]
                        or by_test),
            historical=[],
        )
        self._by_test = by_test
        route = route_of(signals, oracle, forced=self.cfg.route)

        # -- P1 investigate ------------------------------------------------
        self.events.append("phase_start", "P1", {"route": route.name})
        self.log.working("investigating")
        inv = p1_investigate.Investigation(ctx)
        root_cause: RootCauseRecord = inv.run(issue, baseline, sbfl, oracle,
                                              route, ctx.external)
        p1_investigate.apply_gate(root_cause, files, self.cfg, self.log)
        self.log.ok("root cause", root_cause.statement,
                    plain=f"root cause: {root_cause.statement}")
        self.log.cont(f"class={root_cause.classification}  "
                      f"confidence={root_cause.confidence}  "
                      f"files={', '.join(f.path for f in root_cause.files[:2])}")
        self.events.append("phase_end", "P1", root_cause.to_json(),
                           summary=root_cause.statement[:80])
        self._write("rootcause.json", root_cause.to_json())

        # -- P2 scope ------------------------------------------------------
        self.events.append("phase_start", "P2", {})
        plan = p2_scope.scope(ctx, issue, root_cause,
                              list(route.candidates) + self._by_test)
        self.events.append("phase_end", "P2", plan.to_json(),
                           summary=plan.fix_description[:80])
        self._write("scope.json", plan.to_json())

        if self.cfg.dry_run:
            self.log.raw("")
            self.log.raw("dry run: P0-P2 only, nothing was written")
            return exits.NO_FIX

        # -- a test of our own, when the repository has none ---------------
        repro = self._write_repro(ctx, issue, root_cause)

        # -- the fix-and-verify loop (FR-33) -------------------------------
        verifier = p4_verify.Verifier(ctx, baseline)
        impl = p3_implement.Implementation(ctx)
        watch = stuck.StuckState()
        from .watcher import SizeWatcher
        self.sizes = SizeWatcher(log=self.log)
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
            self.log.phase("P3", f"cycle {cycle} of {self.cfg.max_cycles}")
            if not self.log.rich:
                self.log.line(f"cycle {cycle} of {self.cfg.max_cycles}",
                              phase="P3")

            # P3
            try:
                self.log.working(f"implementing (cycle {cycle})")
                applied, flags = impl.run(issue, root_cause, plan, feedback)
            except EditFailure as exc:
                fail = taxonomy.classify_edit_failure(exc)
                self.log.warn(f"{fail.render()}  -> {fail.first_move}")
                self.events.append("degradation", "P3",
                                   {"kind": fail.kind, "detail": fail.detail})

                # The estimate is a guess made before the code was read.
                # When attempt after attempt lands at the same larger size,
                # the guess is what is wrong -- and telling the model to
                # "re-scope tighter" spends every cycle arguing with a
                # number instead of reading the answer it keeps producing.
                if exc.stage == "size":
                    actual = _lines_in(exc.detail)
                    revised = self.sizes.observe(
                        actual, plan.estimated_lines_changed) if actual else None
                    if revised:
                        plan.estimated_lines_changed = revised
                        self.events.append("degradation", "P3",
                                           {"kind": "scope_revised",
                                            "detail": f"estimate -> {revised}"})
                        feedback = ""
                        continue
                # An empty plan is not a transient failure: the next cycle
                # builds the same plan and refuses it the same way. Four
                # identical refusals is how a run spends its whole budget
                # arriving nowhere, with nothing in the report to say why.
                if not plan.files_to_change:
                    summary.status = "NO_FIX"
                    summary.exit_code = exits.NO_FIX
                    summary.reason = (
                        "no file to change: neither the model's plan, the "
                        "root cause, nor the localization signals named a "
                        "file that exists in this repository")
                    self.log.fail("no target", summary.reason,
                                  plain=f"no target: {summary.reason}")
                    break
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
                self.log.working("verifying")
                vres = verifier.run(plan, root_cause, changed, oracle,
                                    final=True)
            except BudgetExceeded:
                self.cfg.conservative = True
                self.log.degraded("budget", "verification cut short")
                break

            # Does our own reproduction pass now? This is the only check
            # that speaks to the fix directly when there is no suite.
            if repro is not None and repro.is_oracle:
                REPRO.confirm(ctx, repro)
                (self.log.ok if repro.verified else self.log.fail)(
                    "reproduction", repro.render(), plain=repro.render())
                self.events.append("repro", "P4",
                                   {"status": repro.status,
                                    "cmd": repro.cmd},
                                   summary=repro.render())

            # P5
            self.log.phase("P5")
            conf = p5_confidence.score(root_cause, plan, vres,
                                       self.workspace, self.cfg, repro=repro)
            self.log.step("ok" if conf.score == 6 else "warn",
                          "confidence", conf.render(),
                          self.log.theme.ok if conf.score == 6
                          else self.log.theme.warn,
                          plain=conf.render())
            action = p5_confidence.decide(conf, cycle, self.cfg.max_cycles)
            self.log.step("ok" if action.value == "SUBMIT" else "warn",
                          "decision", action.value
                          + (f"   blocking: {', '.join(conf.blocking)}"
                             if conf.blocking else ""),
                          plain=f"decision: {action.value}"
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

            # A cycle that produced the same diff AND is blocked on the same
            # conditions cannot produce a different outcome: the prompt is
            # unchanged, so the reply is too -- the log even says "replayed",
            # because it came from the cache. Spending the remaining cycles
            # on it is the most visible waste a run can make.
            signature = (hashlib.sha1(
                self.workspace.diff().encode("utf-8", "replace")).hexdigest(),
                tuple(conf.blocking))
            if signature == getattr(self, "_last_signature", None):
                self.log.line("same diff, same blockers: another cycle cannot "
                              "change the outcome", phase="P5")
                self.events.append("degradation", "P5",
                                   {"kind": "no_progress",
                                    "detail": "identical diff and blockers"})
                break
            self._last_signature = signature

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
                                      list(route.candidates) + self._by_test)
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

        # The reproduction is deleted before the diff is taken, so it can
        # never reach a patch, a report or a pull request by accident.
        if repro is not None:
            summary.repro = repro
            if repro.path is not None or repro.is_oracle:
                REPRO.remove(ctx.repo, ctx.toolchain.language)

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
        # keep what a pull request needs, without re-deriving it
        self.cfg._root_cause = root_cause
        self.cfg._issue = issue
        self.cfg._summary = summary
        # The PR decision needs the evidence, not just the exit code.
        self.cfg._confidence = conf
        self.cfg._verify = vres
        self._record_last_run()
        # The container outlives nothing: stop it before the run reports.
        self._stop_container()

        if self.cfg.github:
            from . import publish
            publish.publish(self.cfg, self.log, self.cfg.github,
                            summary.exit_code, summary, text)
        self.log.done_working()
        self.log.raw("")
        self.log.raw("=== DIFF ===")
        self.log.raw(self.workspace.diff() or "(no changes)")
        return summary.exit_code

    @staticmethod
    def _lexical_signal(ctx, issue) -> list:
        """Files the issue's identifiers actually appear in.

        Paths named in the issue text are the easy case; most issues name a
        symbol or an error string instead, and without this the lexical
        signal is empty whenever coverage is unavailable.
        """
        from .localize.sbfl import is_test_path
        from .repo.search import is_source

        scores: dict = {}
        for path in issue.anchors.files:
            scores[path] = scores.get(path, 0) + 5
        needles = [s.split(".")[-1] for s in issue.anchors.symbols[:5]]
        needles += [e.strip().split(":")[0] for e in issue.anchors.errors[:3]]
        for needle in needles:
            if not needle or len(needle) < 4:
                continue
            for hit in ctx.search.grep(rf"\b{re.escape(needle)}\b",
                                       max_hits=30):
                if not is_source(hit.path) or is_test_path(hit.path):
                    continue
                # A file that DEFINES the symbol outranks one that merely
                # imports or mentions it -- otherwise a package __init__ that
                # re-exports the name wins on hit count alone.
                defines = re.search(
                    rf"^\s*(def|class|func|fn|function)\s+{re.escape(needle)}\b",
                    hit.text)
                scores[hit.path] = scores.get(hit.path, 0) + (8 if defines
                                                              else 1)
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return [p for p, _ in ranked][:6]

    def _clarify_if_vague(self, issue, files):
        """Ask one question rather than guess, when a human is present.

        An issue with no file, symbol or error to hold on to cannot be
        localized, and guessing at it is how a harness produces a confident
        wrong patch. Claude Code asks; so does this, when there is somebody
        to answer. Unattended it still declines -- inventing an answer to its
        own question would be worse than stopping.
        """
        if self.clarify is None:
            return issue
        anchors = issue.anchors
        grounded = bool(anchors.files or anchors.symbols or anchors.errors)
        if grounded or issue.vagueness < 0.7:
            return issue

        question = ("Which file, function or error message should I start "
                    "from?")
        try:
            answer = self.clarify(
                question,
                "The issue does not name a file, a symbol or an error, so "
                "there is nothing to localize from.")
        except Exception:
            return issue
        if not answer or not answer.strip():
            self.log.line("no answer given; continuing with what was provided")
            return issue

        self.cfg.issue = f"{self.cfg.issue.rstrip()}\n\n{answer.strip()}"
        self.events.append("clarified", "P0", {"question": question,
                                               "answer": answer[:400]},
                           summary="operator answered a clarifying question")
        self.log.ok("clarified", answer.strip()[:60],
                    plain=f"clarified: {answer.strip()[:70]}")
        return p0_triage.triage(self.cfg.issue, files,
                                forced_type=self.cfg.task_type)

    def _retry_baseline_in_container(self, baseline):
        """One escalation, driven by what actually happened."""
        from . import container, provision
        from .verify.baseline import capture
        from .verify.runner import active_container

        if not provision.use_environment():
            return baseline      # not asked to prepare anything
        if baseline.tests or not self.toolchain.test_cmd:
            return baseline                      # nothing to explain
        if active_container() is not None or container.mode() == "off":
            return baseline                      # already isolated, or asked not to
        if not container.docker_available():
            self.log.degraded(
                "suite_absent",
                "a suite is configured but ran no tests, and there is no "
                "container to retry it in; verification is weaker")
            return baseline

        self.log.line("the configured suite ran no tests here; retrying in "
                      "the runtime this project declares")
        gap = "the suite does not run on this host"
        decision = provision._try_container(self.cfg.repo_path, gap, self)
        if decision is None:
            return baseline

        self.provision = decision
        self.container = decision.container
        self.toolchain = discover_toolchain(self.cfg.repo_path, self.cfg)
        self.events.append("dependencies", "P0", decision.to_json(),
                           summary=f"retry in {decision.detail}")
        from .verify import deps
        if self.gate is not None:
            deps.ensure(self.cfg.repo_path, self.toolchain, self.gate,
                        self.log)
        retried = capture(self.cfg.repo_path, self.toolchain, self.cfg,
                          self.log, self.run_dir)
        if retried.tests:
            return retried
        self.log.degraded("suite_absent",
                          "the suite ran no tests on the host or in a "
                          "container; verification is weaker")
        return retried

    def _provision(self):
        """Walk the environment ladder: host, venv, container, install."""
        from . import provision
        try:
            decision = provision.provision(self.cfg.repo_path, self)
        except Exception as exc:        # setup must never end a run
            self.log.degraded("provision", str(exc))
            from .provision import Decision
            return Decision()
        if decision.gap:
            self.log.computed("environment", decision.render(),
                              plain=f"environment: {decision.render()}")
            self.events.append("dependencies", "P0", decision.to_json(),
                               summary=decision.render())
            # Discovery ran against the old environment.
            self.toolchain = discover_toolchain(self.cfg.repo_path, self.cfg)
        return decision

    def _start_container(self):
        """Run the repository's commands in the runtime it declares.

        Not the host's. A project pinning Node 22 or Python 3.11 is not
        served by whatever happens to be installed, and installing its
        dependencies onto the operator's machine to find out is not a
        neutral act either.
        """
        from . import consent, container
        from .verify import runner

        want = container.mode()
        if want == "off":
            return None
        if not container.docker_available():
            if want == "always":
                self.log.degraded("no_docker",
                                  "HARNESS_DOCKER=always but Docker is not "
                                  "running; continuing on the host")
            return None

        # A container answers a missing tool. Where the host can already
        # build and test the repository, moving into a bare image takes away
        # the interpreter that had the dependencies and gains nothing.
        if want == "auto":
            satisfied = container.host_satisfies(self.cfg.repo_path,
                                                 self.toolchain)
            if not satisfied:
                return None
            self.log.line(f"host cannot build this repository: {satisfied}")

        env = container.detect(self.cfg.repo_path, self.toolchain)
        if not env.available:
            if want == "always":
                self.log.degraded("no_image", env.reason)
            return None

        if self.gate is not None and not self.gate.allow(
                consent.INSTALL, f"run this repository in {env.image}",
                [("image", env.image), ("chosen from", env.why),
                 ("mounted at", container.WORKDIR)], requested=True):
            self.log.degraded("no_container", "declined; using the host")
            return None

        box = container.Container(repo=self.cfg.repo_path, image=env.image)
        if not box.start(self.log):
            return None
        runner.use_container(box)
        # Discovery ran against the host: a host virtualenv path means
        # nothing inside the container.
        self.toolchain = discover_toolchain(self.cfg.repo_path, self.cfg)
        self.events.append("dependencies", "P0",
                           {"image": env.image, "why": env.why},
                           summary=f"container {env.image}")
        return box

    def _stop_container(self) -> None:
        from .verify import runner
        box = getattr(self, "container", None)
        if box is not None:
            runner.use_container(None)
            box.stop()
            self.container = None

    def _install_dependencies(self):
        """A cloned repository has no node_modules and no virtualenv."""
        from . import provision
        from .verify import deps
        if not provision.use_environment():
            return deps.Install(needed=False)
        if self.gate is None:
            return deps.Install(needed=False)
        try:
            plan = deps.ensure(self.cfg.repo_path, self.toolchain,
                               self.gate, self.log)
        except Exception as exc:        # never let setup end the run
            self.log.degraded("deps", f"could not install: {exc}")
            return deps.Install(needed=False)
        if plan.needed:
            self.events.append("dependencies", "P0",
                               {"cmd": plan.cmd, "ok": plan.ok,
                                "ran": plan.ran},
                               summary=plan.render())
            # The toolchain was discovered against a repository that could
            # not run anything; re-read it now that it can.
            if plan.ok:
                self.toolchain = discover_toolchain(self.cfg.repo_path,
                                                    self.cfg)
        return plan

    # -- a test of our own -------------------------------------------------
    def _write_repro(self, ctx, issue, root_cause):
        """Write a failing test when the repository has none.

        HARNESS_REPRO: `auto` (default) writes one only when no suite was
        discovered, `always` writes one regardless, `off` never does. The
        default is deliberately narrow -- where a suite already exists it is
        the better evidence, and writing a test costs a model call.
        """
        raw = (os.environ.get("HARNESS_REPRO") or "").strip().lower()
        if raw in ("off", "0", "no", "false", "never"):
            return None
        # An unrecognised value falls back to the default, never to the more
        # expensive setting: a typo must not silently start spending calls.
        mode = "always" if raw in ("always", "1", "yes", "true", "on") \
            else "auto"
        has_suite = bool(ctx.toolchain.test_cmd)
        if mode == "auto" and has_suite:
            return None

        self.log.raw("")
        self.log.phase("P2", "writing a reproduction test" if has_suite else
                       "writing a test: this repository has none")
        self.log.working("writing a reproduction")
        try:
            rep = REPRO.attempt(ctx, issue, root_cause,
                                self._repro_context(ctx, root_cause))
        except Exception as exc:        # scaffolding must never end a run
            self.log.degraded("repro", f"could not write a test: {exc}")
            return None
        finally:
            self.log.done_working()

        if rep.is_oracle:
            self.log.ok("reproduction", f"{rep.render()}  ({rep.cmd})",
                        plain=f"reproduction: {rep.render()} [{rep.cmd}]")
        else:
            # Not a failed run: this gate is simply unavailable.
            self.log.degraded("repro", rep.render())
        self.events.append("repro_written", "P2",
                           {"status": rep.status, "cmd": rep.cmd,
                            "attempts": rep.attempts},
                           summary=rep.render())
        return rep

    def _repro_context(self, ctx, root_cause) -> str:
        """The files the test must import, with the paths it should use."""
        parts = []
        seen = []
        for ev in (getattr(root_cause, "evidence", None) or []):
            path = getattr(ev, "path", None)
            if path and path not in seen:
                seen.append(path)
        for path in (getattr(root_cause, "files", None) or []):
            if path not in seen:
                seen.append(path)
        for path in seen[:3]:
            try:
                text = (ctx.repo / path).read_text("utf-8", errors="replace")
            except (OSError, TypeError, ValueError):
                continue
            parts.append(f"--- {path} ---\n{text[:4000]}")
        return "\n\n".join(parts)

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
            # The reproduction test is the specification. When it is still
            # red, its own output is the most useful thing we can say -- far
            # better than naming the condition that failed, which the model
            # cannot act on.
            if vres is not None and vres.oracle_passes is False:
                name = (vres.oracle_id or "the reproduction test")
                out = (vres.oracle_output or "").strip()
                return (f"Your change did NOT make the test pass. "
                        f"`{name}` still fails:\n\n{out[-1200:]}\n\n"
                        f"Read the assertion above and satisfy it exactly. "
                        f"Do not guess at the boundary -- the test states it.")
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

    def _record_last_run(self) -> None:
        """Leave a pointer where `make test` runs, so replay needs no args.

        A run's artifacts live in the TARGET repository, which may be nowhere
        near the directory `make test` is invoked from.
        """
        try:
            pointer = Path.cwd() / ".harness"
            pointer.mkdir(parents=True, exist_ok=True)
            (pointer / "last_run.json").write_text(json.dumps({
                "repo": str(self.cfg.repo_path),
                "trajectory": str(self.run_dir / "trajectory.jsonl"),
            }), encoding="utf-8")
        except OSError:
            pass

    def _write(self, name: str, payload: dict) -> None:
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            (self.run_dir / name).write_text(
                json.dumps(payload, indent=2, default=str), encoding="utf-8")
        except OSError:
            pass
