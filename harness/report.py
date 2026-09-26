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
    added, removed = workspace.diff_numstat()
    changed = workspace.changed_files()
    log.raw("")
    log.rule("RESULT")
    log.raw(f"status            {summary.status}")
    if summary.reason:
        log.raw(f"reason            {summary.reason}")
    log.raw(f"root cause        {root_cause.statement[:70]}")
    log.raw(f"classification    {root_cause.classification:<16} "
            f"confidence  {root_cause.confidence}")
    log.raw(f"files changed     {', '.join(changed) or 'none'}  "
            f"(+{added} -{removed})")

    if verify:
        cls = verify.full or verify.scoped
        if cls:
            log.raw(f"tests             {len(cls.fixed)} now passing - "
                    f"{len(cls.blocking)} new failures - "
                    f"{len(cls.pre_existing)} pre-existing (documented)")
            for tid in cls.pre_existing[:3]:
                log.raw(f"pre-existing      {tid}")
            for tid in cls.flaky[:2]:
                log.raw(f"flaky             {tid}")
        log.raw(f"lint              "
                f"{verify.lint.render().splitlines()[0].replace('lint: ', '')}")
    if confidence:
        log.raw(f"confidence        {confidence.render()}")
    t = budgets.tokens
    log.raw(f"cycles used       {summary.cycles} of {cfg.max_cycles}"
            f"        tokens {_k(t.used)}"
            f"        wall {budgets.clock.elapsed:.0f}s")
    if t.used_cached:
        log.raw(f"prompt cache      {t.cache_ratio()*100:.0f}% of input tokens "
                f"served from cache")
    log.rule()


def write(run_dir: Path, text: str) -> None:
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run_report.md").write_text(text, encoding="utf-8")
    except OSError:
        pass
