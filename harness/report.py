"""The evidence package (SPEC.md 11.3, 30.1, FR-36).

One narrative artifact the evaluator reads, printed to stdout and written to
.harness/run/run_report.md.  Sections 5 (what was NOT changed and why) and 8
(cost) are the ones no competing harness will have, and they cost nothing:
the data is already in the trajectory.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import exits


@dataclass
class RunSummary:
    status: str = "NO_FIX"
    exit_code: int = exits.NO_FIX
    reason: str = ""
    cycles: int = 0
    attempts: int = 0


def _k(n: int) -> str:
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


def build(cfg, log, summary, issue, root_cause, plan, verify, confidence,
          workspace, budgets, route=None, notes=None) -> str:
    """Return the markdown report. Also renders the stdout block."""
    added, removed = workspace.diff_numstat()
    changed = workspace.changed_files()
    lines: list[str] = []
    w = lines.append

    w(f"# Run report - {summary.status}")
    w("")
    w("## 1. Task")
    w(f"- type: `{issue.task_type}`   vagueness: {issue.vagueness}")
    w(f"- issue: {issue.title}")
    w(f"- anchors:\n```\n{issue.anchors.render()}\n```")

    w("")
    w("## 2. Investigation")
    if route:
        w(f"- route: **{route.name}** - {route.reason}")
    for e in (root_cause.evidence or [])[:12]:
        w(f"- {e.render()}")
    if root_cause.hypotheses:
        w("")
        w("Hypotheses, each with the check the harness executed:")
        w("```")
        for h in root_cause.hypotheses:
            w(h.render())
        w("```")

    w("")
    w("## 3. Root cause")
    w(f"- **{root_cause.statement}**")
    w(f"- classification: `{root_cause.classification}`   "
      f"confidence: `{root_cause.confidence}`")
    for f in root_cause.files[:5]:
        w(f"- `{f.path}:{f.lines[0]}-{f.lines[1]}` {f.why}")

    w("")
    w("## 4. What changed")
    if changed:
        for path in changed:
            w(f"- `{path}`")
        w("")
        w("```diff")
        w(workspace.diff()[:8000])
        w("```")
    else:
        w("- nothing was changed")

    w("")
    w("## 5. What was NOT changed, and why")
    for path in sorted(plan.forbidden)[:8]:
        w(f"- `{path}` - on the must-not-change list")
    for alt in (root_cause.alternatives_rejected or [])[:5]:
        w(f"- alternative {alt.get('id')} rejected: {alt.get('reason','')}")
    if verify and verify.full and verify.full.pre_existing:
        for tid in verify.full.pre_existing[:6]:
            w(f"- `{tid}` was already failing before this run - documented, "
              f"not fixed")
    if len(lines) and lines[-1].startswith("## 5"):
        w("- nothing excluded")

    w("")
    w("## 6. Verification")
    if verify:
        w(f"- lint: {verify.lint.render().splitlines()[0]}")
        if verify.oracle_passes is not None:
            w(f"- reproduction test: "
              f"{'passes' if verify.oracle_passes else 'still fails'}")
        for label, cls in (("scoped", verify.scoped), ("full suite",
                                                       verify.full)):
            if not cls:
                continue
            w(f"- {label}: {len(cls.new)} new, {len(cls.pre_existing)} "
              f"pre-existing, {len(cls.fixed)} now passing, "
              f"{len(cls.flaky)} flaky")
            for tid in cls.new[:5]:
                w(f"  - NEW FAILURE `{tid}`")
        w(f"- {verify.judge.render()}")
        if verify.practices and verify.practices.issues:
            for i in verify.practices.issues[:4]:
                w(f"- advisory: {i}")

    w("")
    w("## 7. Confidence")
    if confidence:
        for i, cond in enumerate(confidence.CONDITIONS, 1):
            mark = "ok" if getattr(confidence, cond) else "FAIL"
            w(f"- C{i} {cond}: **{mark}**")
        w(f"- overall: **{confidence.overall}** ({confidence.score}/6)")

    w("")
    w("## 8. Cost")
    t = budgets.tokens
    w(f"- tokens: {_k(t.used_in)} in, {_k(t.used_out)} out, "
      f"{_k(t.used_cached)} cache-read "
      f"({t.cache_ratio()*100:.0f}% of input served from cache)")
    w(f"- wall clock: {budgets.clock.elapsed:.0f}s of {budgets.clock.limit_s:.0f}s")
    w(f"- cycles: {summary.cycles} of {cfg.max_cycles}")
    w(f"- diff: +{added} -{removed} across {len(changed)} file(s)")
    if t.per_phase:
        w("- per phase:")
        for phase in sorted(t.per_phase):
            s = t.per_phase[phase]
            w(f"  - {phase}: {s['calls']} call(s), "
              f"{_k(s['in'])} in / {_k(s['out'])} out")
    for n in (notes or []):
        w(f"- note: {n}")

    return "\n".join(lines) + "\n"


def render_stdout(log, cfg, summary, root_cause, plan, verify, confidence,
                  workspace, budgets) -> None:
    """The SPEC.md 11.3 block."""
    from .ui import estimate_cost, human_time
    added, removed = workspace.diff_numstat()
    changed = workspace.changed_files()
    log.raw("")

    if log.rich:
        t = log.theme
        colour = {"SUCCESS": t.ok, "PARTIAL": t.warn}.get(summary.status,
                                                          t.bad)
        mark = {"SUCCESS": log.g.OK, "PARTIAL": log.g.WARN}.get(
            summary.status, log.g.BAD)
        bar = ("\u2500" * 62) if log.unicode else ("-" * 62)
        tl, tr = ("\u256d", "\u256e") if log.unicode else ("+", "+")
        bl, br = ("\u2570", "\u256f") if log.unicode else ("+", "+")
        v = "\u2502" if log.unicode else "|"
        title = f"{t.paint(mark, colour)} {t.bold}{summary.status}{t.reset}"
        pad = " " * max(1, 61 - len(summary.status) - 3)
        log.raw(f"  {colour}{tl}{bar}{tr}{t.reset}")
        log.raw(f"  {colour}{v}{t.reset} {title}{pad}{colour}{v}{t.reset}")
        log.raw(f"  {colour}{bl}{bar}{br}{t.reset}")
    else:
        log.rule("RESULT")
    W = 18
    if not log.rich:
        log.raw(f"status            {summary.status}")
    if summary.reason:
        log.kv("reason", summary.reason, log.theme.dim, W)
    log.kv("root cause", root_cause.statement[:70], log.theme.bold, W)
    log.kv("classification",
           f"{root_cause.classification:<16} confidence  "
           f"{root_cause.confidence}", "", W)
    log.kv("files changed",
           f"{', '.join(changed) or 'none'}  (+{added} -{removed})", "", W)

    if verify:
        cls = verify.full or verify.scoped
        if cls:
            log.kv("tests", f"{len(cls.fixed)} now passing - "
                            f"{len(cls.blocking)} new failures - "
                            f"{len(cls.pre_existing)} pre-existing "
                            f"(documented)",
                   log.theme.bad if cls.blocking else log.theme.ok, W)
            for tid in cls.pre_existing[:3]:
                log.kv("pre-existing", tid, log.theme.dim, W)
            for tid in cls.flaky[:2]:
                log.kv("flaky", tid, log.theme.dim, W)
        log.kv("lint",
               verify.lint.render().splitlines()[0].replace("lint: ", ""),
               "", W)
    if confidence:
        log.kv("confidence", confidence.render(),
               log.theme.ok if confidence.score == 6 else log.theme.warn, W)
    tb = budgets.tokens
    log.kv("cycles used", f"{summary.cycles} of {cfg.max_cycles}"
                          f"        tokens {_k(tb.used)}"
                          f"        wall {budgets.clock.elapsed:.0f}s",
           log.theme.dim, W)
    if tb.used_cached:
        log.kv("prompt cache",
               f"{tb.cache_ratio()*100:.0f}% of input tokens served from "
               f"cache", log.theme.dim, W)
    model = getattr(getattr(cfg, "_primary", None), "id", "") or cfg.model or ""
    cost = estimate_cost(model, tb.used_in, tb.used_out)
    if cost is not None:
        log.kv("estimated cost", f"${cost:.3f}  (list price, {model})",
               log.theme.dim, W)
    if not log.rich:
        log.rule()


def write(run_dir: Path, text: str) -> None:
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run_report.md").write_text(text, encoding="utf-8")
    except OSError:
        pass
