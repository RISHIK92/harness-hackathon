"""Phase 1: root-cause investigation (FR-12..FR-17, SPEC.md 7.2, 21).

The single most important property: this phase CANNOT produce code.  It is
handed no write tool and no edit tool -- enforced structurally, not by prompt
wording.  The harness executes every hypothesis check; the model proposes and
interprets, and the harness decides what is true.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..context.assemble import system_prompt, wrap_untrusted
from ..localize.router import DIRECT, FULL, ORACLE
from ..records import (AffectedFile, Check, Evidence, Hypothesis,
                       RecordError, RootCauseRecord)
from ..repo import history as H
from ..localize.sbfl import is_test_path
from ..repo.search import is_source
from ..repo.snippets import read_window, symbols
from ..structured import ParseFailure, ask_structured, extract_json
from ..verify.runner import run

INVESTIGATION_ROLE = "investigation"

SYSTEM_EXTRA = """\
You are performing root cause analysis. Your ONLY job is to understand the
bug. You CANNOT write, edit, or create files, and you CANNOT propose fixes.

Method, in order:
  1. Locate where the symptom appears.
  2. Trace upstream from there. What calls this? What supplies its inputs?
     The bug is often two or three levels above where the error surfaces.
  3. Read what recently changed in the suspect files.
  4. For each competing explanation, state a check that would DISPROVE it.

Do not produce conclusions from a hypothesis. Produce them from evidence."""

HYPOTHESIS_PROMPT = """\
Propose exactly {n} competing explanations for this bug.

Each explanation MUST carry a check the harness can execute mechanically.
Allowed check kinds:
  grep    arg = a regular expression to search the repository for
  read    arg = "path:line" to read a window of source
  git     arg = a path whose recent history should be inspected
  env     arg = an environment variable name to test for presence
  version arg = a package name whose declared vs pinned version to compare

Reply with ONLY this JSON:
{{"hypotheses": [
  {{"id": "H1", "statement": "...", "predicts": "what must be true if this is the cause",
    "check": {{"kind": "grep", "arg": "..."}}}}
]}}"""

SYNTHESIS_PROMPT = """\
Produce the ROOT CAUSE REPORT from the evidence above.

Reply with ONLY this JSON:
{{"statement": "one sentence, specific, no hedging",
 "classification": "one of: {classes}",
 "files": [{{"path": "<an existing repository path>", "lines": [start, end], "why": "..."}}],
 "confidence": "low | medium | high",
 "external_factor": true or false}}"""


class Investigation:
    def __init__(self, ctx) -> None:
        self.ctx = ctx                 # PhaseContext: cfg, log, router, repo...
        self.evidence: list[Evidence] = []
        self.steps = 0

    # -- public ------------------------------------------------------------
    def run(self, issue, baseline, sbfl, oracle, route, external) -> RootCauseRecord:
        c = self.ctx
        c.log.phase("P1")
        c.log.line(f"{route.render()}")

        self._seed(issue, sbfl, external)

        if route.name == ORACLE:
            return self._from_oracle(issue, oracle, sbfl, route, external)
        if route.name == DIRECT:
            return self._from_convergence(issue, sbfl, route, external)
        return self._full(issue, sbfl, route, external, baseline)

    # -- seeding (all deterministic) ---------------------------------------
    def _seed(self, issue, sbfl, external) -> None:
        c = self.ctx
        for err in issue.anchors.errors[:3]:
            needle = err.strip().split("\n")[0][:60]
            if len(needle) < 5:
                continue
            hits = c.search.grep(re.escape(needle), max_hits=8)
            c.log.line(f'grep "{needle[:44]}" -> {len(hits)} hits')
            if hits:
                self.evidence.append(Evidence(
                    "grep", f"{needle[:60]} found at " +
                    ", ".join(h.render() for h in hits[:3])))

        for sym in issue.anchors.symbols[:4]:
            name = sym.split(".")[-1]
            if len(name) < 3:
                continue
            hits = c.search.grep(rf"\b{re.escape(name)}\b", max_hits=10)
            if hits:
                self.evidence.append(Evidence(
                    "grep", f"{name} referenced at " +
                    ", ".join(h.render() for h in hits[:3])))

        if external.checked:
            c.log.line(f"probe: {len(external.checked)} external checks, "
                       f"{len(external.findings)} finding(s)")
            for f in external.findings[:4]:
                c.log.cont(f.render())
                self.evidence.append(Evidence("probe", f.render()))

        for path in self._suspect_files(issue, sbfl)[:3]:
            fh = H.file_history(c.repo, path)
            if fh.commits:
                c.log.line(f"git log {path} -> {len(fh.commits)} commits"
                           + ("  [recent]" if fh.recent else ""))
                self.evidence.append(Evidence("git", fh.render(3)))

        if sbfl.ok:
            c.log.line("coverage localization:")
            for s in sbfl.lines[:3]:
                c.log.cont(s.render())
            self.evidence.append(Evidence(
                "sbfl", "; ".join(s.render() for s in sbfl.lines[:3])))

    def _suspect_files(self, issue, sbfl) -> list[str]:
        out = list(sbfl.top_files(5)) if sbfl.ok else []
        out += [f for f in issue.anchors.files if f not in out]
        out += [f for f, _l, _fn in issue.anchors.frames if f not in out]
        return out

    def upstream(self, path: str, symbol: str, depth: int = 2) -> list[str]:
        """Trace callers. The bug is often two or three frames above."""
        c = self.ctx
        seen: list[str] = []
        frontier = [symbol]
        for _ in range(depth):
            nxt = []
            for name in frontier:
                hits = c.search.grep(rf"\b{re.escape(name)}\s*\(", max_hits=20)
                for h in hits:
                    if not is_source(h.path):
                        continue
                    if h.path == path and name == symbol:
                        continue
                    caller = self._enclosing(h.path, h.line)
                    entry = f"{h.path}:{h.line} in {caller or '?'}"
                    if entry not in seen:
                        seen.append(entry)
                        if caller:
                            nxt.append(caller)
            frontier = nxt[:4]
            if not frontier:
                break
        return seen[:12]

    def _enclosing(self, path: str, line: int) -> str:
        try:
            for sym in symbols(self.ctx.repo / path):
                if sym.start <= line <= sym.end and sym.kind != "class":
                    return sym.qualname
        except Exception:
            pass
        return ""

    # -- routes ------------------------------------------------------------
    def _from_oracle(self, issue, oracle, sbfl, route, external) -> RootCauseRecord:
        """A failing test is ground truth; hypothesis elimination is skipped."""
        c = self.ctx
        c.log.line(f"oracle test: {oracle.test_id}")
        c.log.cont(oracle.reason)
        self.evidence.append(Evidence(
            "test", f"{oracle.test_id} fails on unmodified code ({oracle.reason})"))

        top = sbfl.lines[0] if sbfl.ok and sbfl.lines else None
        if top:
            callers = self.upstream(top.path, top.symbol.split(".")[-1]) \
                if top.symbol else []
            if callers:
                c.log.line("upstream: " + " <- ".join(callers[:3]))
                self.evidence.append(Evidence("read", "callers: "
                                              + "; ".join(callers[:5])))
        return self._synthesize(issue, sbfl, route, external, oracle=oracle)

    def _from_convergence(self, issue, sbfl, route, external) -> RootCauseRecord:
        c = self.ctx
        c.log.line(f"signals converge on {route.winner}")
        self.evidence.append(Evidence("sbfl", route.reason))
        return self._synthesize(issue, sbfl, route, external)

    def _full(self, issue, sbfl, route, external, baseline) -> RootCauseRecord:
        """Hypotheses with executable checks, eliminated by the harness."""
        c = self.ctx
        hyps = self._propose(issue, sbfl, route, issue.min_hypotheses)
        rounds = 0
        while rounds < 2:
            for h in hyps:
                if h.support != "untested":
                    continue
                h.result, h.support = self._execute(h)
                c.log.line(f"{h.id} {h.statement[:56]}")
                c.log.cont(f"check: {h.check.kind} {h.check.arg[:40]}"
                           f"  -> {h.support.upper()}")
                self.evidence.append(Evidence(
                    "grep" if h.check.kind == "grep" else h.check.kind,
                    f"{h.id}: {h.statement[:80]} -> {h.support}"))
            if any(h.support == "confirmed" for h in hyps):
                break
            rounds += 1
            if rounds < 2 and c.budget_ok("P1"):
                c.log.line("all hypotheses refuted; regenerating with evidence")
                hyps += self._propose(issue, sbfl, route, 2, refuted=hyps)
            else:
                break

        rec = self._synthesize(issue, sbfl, route, external, hypotheses=hyps)
        rec.hypotheses = hyps
        rec.alternatives_rejected = [
            {"id": h.id, "reason": (h.result or "refuted")[:160]}
            for h in hyps if h.support == "refuted"]
        return rec

    # -- model steps -------------------------------------------------------
    def _propose(self, issue, sbfl, route, n, refuted=None) -> list[Hypothesis]:
        c = self.ctx
        parts = [wrap_untrusted(issue.raw, "issue"),
                 "Anchors extracted from the issue:\n" + issue.anchors.render()]
        if sbfl.ok:
            parts.append("Coverage points at these lines (measured, not "
                         "guessed):\n" + sbfl.render())
        if route.candidates:
            parts.append("Candidate files:\n" + "\n".join(
                f"  {i+1}. {p}" for i, p in enumerate(route.candidates)))
        if refuted:
            parts.append("Already refuted, do not repeat:\n" + "\n".join(
                f"  {h.id} {h.statement} -> {h.result}"
                for h in refuted if h.support == "refuted"))
        parts.append(HYPOTHESIS_PROMPT.format(n=n))

        messages = [
            {"role": "system", "content": system_prompt(
                INVESTIGATION_ROLE, c.cfg.effective_tier, SYSTEM_EXTRA)},
            {"role": "user", "content": "\n\n".join(parts)},
        ]
        try:
            data = ask_structured(c.router, "p1_hypotheses", "P1", messages,
                                  ["hypotheses"], c.log, cache_prefix=1)
        except ParseFailure as exc:
            c.log.warn(f"hypothesis generation failed: {exc.detail}")
            return []

        out: list[Hypothesis] = []
        for i, raw in enumerate(data.get("hypotheses", [])[:6]):
            if not isinstance(raw, dict):
                continue
            chk = raw.get("check") or {}
            kind = str(chk.get("kind", "grep")).lower()
            arg = str(chk.get("arg", "")).strip()
            if kind not in ("grep", "read", "test", "git", "env", "version") \
                    or not arg:
                continue                       # no executable check: rejected
            out.append(Hypothesis(
                id=str(raw.get("id") or f"H{len(out)+1+i}"),
                statement=str(raw.get("statement", ""))[:300],
                predicts=str(raw.get("predicts", ""))[:200],
                check=Check(kind, arg[:200])))
        return out

    def _execute(self, h: Hypothesis) -> tuple[str, str]:
        """The HARNESS decides what is true. Never the model."""
        c = self.ctx
        k, arg = h.check.kind, h.check.arg
        try:
            if k == "grep":
                hits = c.search.grep(arg, max_hits=20)
                if hits:
                    return (f"{len(hits)} match(es): "
                            + "; ".join(h2.render() for h2 in hits[:3]),
                            "confirmed")
                return "no match in the repository", "refuted"

            if k == "read":
                path, _, line = arg.partition(":")
                window = read_window(c.repo / path.strip(),
                                     int(line or 1), 6, 12)
                return (window[:800], "inconclusive" if not window
                        else "confirmed")

            if k == "git":
                fh = H.file_history(c.repo, arg.strip())
                if fh.commits:
                    return fh.render(3), "confirmed" if fh.recent else "inconclusive"
                return "no history for that path", "refuted"

            if k == "env":
                import os
                name = arg.strip().strip("'\"")
                present = name in os.environ
                return (f"{name} is {'set' if present else 'NOT set'}",
                        "refuted" if present else "confirmed")

            if k == "version":
                findings = [f for f in c.external.findings
                            if arg.lower() in f.detail.lower()]
                if findings:
                    return findings[0].detail, "confirmed"
                return f"no version skew found for {arg}", "refuted"

            if k == "test":
                r = run(arg, c.repo, timeout=120, check_deny=False)
                return (r.output[-800:],
                        "confirmed" if r.exit_code != 0 else "refuted")
        except Exception as exc:
            return f"check could not run: {exc}", "inconclusive"
        return "unsupported check kind", "inconclusive"

    def _synthesize(self, issue, sbfl, route, external, hypotheses=None,
                    oracle=None) -> RootCauseRecord:
        c = self.ctx
        from ..records import BUG_CLASSES

        parts = [wrap_untrusted(issue.raw, "issue"),
                 "Evidence gathered (all of it executed by the harness):",
                 "\n".join("  " + e.render() for e in self.evidence[:14])]
        if external.findings:
            parts.append("Measured external factors:\n" + external.render())
        else:
            parts.append(external.render())
        if hypotheses:
            parts.append("Hypotheses and their verdicts:\n"
                         + "\n".join(h.render() for h in hypotheses))
        if oracle:
            parts.append(f"A failing test reproduces the issue: {oracle.test_id}")
        if sbfl.ok:
            parts.append("Most suspicious lines:\n" + sbfl.render())
        parts.append(SYNTHESIS_PROMPT.format(classes=" | ".join(BUG_CLASSES)))

        messages = [
            {"role": "system", "content": system_prompt(
                INVESTIGATION_ROLE, c.cfg.effective_tier, SYSTEM_EXTRA)},
            {"role": "user", "content": "\n\n".join(parts)},
        ]
        try:
            data = ask_structured(
                c.router, "p1_synthesis", "P1", messages,
                ["statement", "classification", "confidence"], c.log,
                cache_prefix=1)
        except ParseFailure as exc:
            c.log.warn(f"synthesis failed: {exc.detail}")
            data = {}

        rec = RootCauseRecord(
            statement=str(data.get("statement", "")).strip(),
            classification=_norm_class(data.get("classification"), external),
            confidence=_norm_conf(data.get("confidence")),
            evidence=self.evidence,
            external_factors=external.to_json(),
            route=route.name,
        )
        for f in data.get("files", [])[:5]:
            if isinstance(f, dict) and f.get("path"):
                lines = f.get("lines") or [0, 0]
                rec.files.append(AffectedFile(
                    str(f["path"]).lstrip("./"),
                    (int(lines[0]), int(lines[1])) if len(lines) == 2 else (0, 0),
                    str(f.get("why", ""))[:200]))

        # A test file is evidence, never the thing to fix. Dropping it here
        # keeps the scope plan from inheriting a target it must not touch.
        known = set(c.search.files())
        source_files = [f for f in rec.files
                        if not is_test_path(f.path) and f.path in known]
        if source_files != rec.files:
            dropped = [f.path for f in rec.files if is_test_path(f.path)]
            c.log.debug(f"dropped test path(s) from the root cause: {dropped}")
            rec.files = source_files

        if not rec.files and sbfl.ok and sbfl.lines:
            top = sbfl.lines[0]
            rec.files.append(AffectedFile(top.path, (top.line, top.line),
                                          "highest coverage suspiciousness"))
        if not rec.statement and sbfl.ok and sbfl.lines:
            rec.statement = (f"The failure originates at "
                             f"{sbfl.lines[0].path}:{sbfl.lines[0].line}")
            rec.confidence = "low"
        return rec


def _norm_class(value, external) -> str:
    from ..records import BUG_CLASSES
    v = str(value or "").strip().lower().replace("-", "_")
    for c in BUG_CLASSES:
        if c in v:
            return c
    strong = [f for f in external.findings if f.severity == "high"]
    if strong:
        kinds = {f.kind for f in strong}
        if "env" in kinds or "config" in kinds:
            return "config"
        if "dependency" in kinds or "runtime" in kinds:
            return "external"
    return "logic"


def _norm_conf(value) -> str:
    v = str(value or "").strip().lower()
    for c in ("high", "medium", "low"):
        if c in v:
            return c
    return "medium"


def apply_gate(rec: RootCauseRecord, known_files: set[str], cfg, log) -> None:
    """FR-17: a failed gate forces the conservative path, never a guess."""
    try:
        rec.validate(known_files)
    except RecordError as exc:
        log.warn(f"root-cause gate: {exc}")
        rec.confidence = "low"
    if rec.confidence == "low":
        cfg.conservative = True
        log.line("confidence is low -> conservative mode: "
                 "<=15 lines, 1 file, no signature changes")
