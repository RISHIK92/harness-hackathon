"""Phase 2: change scoping (FR-18..FR-21, SPEC.md 7.3).

The plan is a binding contract the implementer cannot exceed.  The model
proposes it; the harness HARDENS it, because a model that forgets one caller
produces a plausible-looking broken change.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..context.assemble import system_prompt, wrap_untrusted
from ..records import ChangePlan, FileIntent, RecordError
from ..repo.search import is_source
from ..repo.snippets import symbols
from ..structured import ParseFailure, ask_structured

SCOPE_ROLE = "scoping"

NEVER_TOUCH = re.compile(
    r"(^|/)(tests?|spec|__tests__|fixtures?)/|_test\.|test_|\.test\.|_spec\.|"
    r"(^|/)(package-lock\.json|yarn\.lock|poetry\.lock|Cargo\.lock|go\.sum|"
    r"requirements\.lock|Gemfile\.lock|composer\.lock)$|"
    r"(^|/)(vendor|node_modules|dist|build|migrations)/|"
    r"\.(generated|pb|min)\.|_pb2\.py$")

SCOPE_PROMPT = """\
Produce the change plan. It is a binding contract: the implementation stage
cannot touch anything outside it.

Reply with ONLY this JSON:
{{"fix_description": "one paragraph of plain English",
 "files_to_change": [{{"path": "<a repository path>", "symbol": "function name or null",
                      "intent": "what changes here", "new_file": false}}],
 "files_must_not_change": ["..."],
 "interface_changes": true or false,
 "estimated_lines_changed": <integer>,
 "fix_classification": "minimal_edit | function_rewrite | cross_file"}}

Constraints:
- {limit}
- Name files that exist, unless the fix genuinely needs a new one: then give
  the path it should have and set "new_file": true. Prefer editing an
  existing file; a new module is for when there is nowhere sensible to put
  the code.
- Never list a test file, lockfile, or generated file under files_to_change.
- estimated_lines_changed is the total of added plus removed lines."""


def callers_of(ctx, symbol: str, defining_file: str) -> list[str]:
    """FR-20: actual call sites, not merely importing files.

    An import graph tells you which files import a module; it does not tell
    you which lines call the function whose signature is changing.
    """
    if not symbol:
        return []
    sites: list[str] = []
    hits = ctx.search.grep(rf"\b{re.escape(symbol)}\s*\(", max_hits=80)
    for h in hits:
        if h.path == defining_file or not is_source(h.path):
            continue
        if NEVER_TOUCH.search(h.path):
            continue
        entry = f"{h.path}:{h.line}"
        if entry not in sites:
            sites.append(entry)
    return sites[:20]


def scope(ctx, issue, root_cause, candidates=None) -> ChangePlan:
    c = ctx
    c.log.phase("P2")

    limit = ("This is a CONSERVATIVE fix: at most 15 changed lines in exactly "
             "one file, and no signature or interface changes."
             if c.cfg.conservative else
             "Produce the smallest change that resolves the root cause.")

    suspects = [f.path for f in root_cause.files][:5]
    parts = [
        wrap_untrusted(issue.raw, "issue"),
        f"ROOT CAUSE: {root_cause.statement}",
        f"CLASSIFICATION: {root_cause.classification}   "
        f"CONFIDENCE: {root_cause.confidence}",
        "Affected files identified during investigation:\n" + "\n".join(
            f"  {f.path}:{f.lines[0]}-{f.lines[1]} {f.why}"
            for f in root_cause.files[:5]) if root_cause.files else "",
        SCOPE_PROMPT.format(limit=limit),
    ]
    messages = [
        {"role": "system", "content": system_prompt(
            SCOPE_ROLE, c.cfg.effective_tier,
            "You scope a change before it is written. You do not write code.")},
        {"role": "user", "content": "\n\n".join(p for p in parts if p)},
    ]

    try:
        data = ask_structured(c.router, "p2_scope", "P2", messages,
                              ["fix_description", "files_to_change"], c.log,
                              cache_prefix=1)
    except ParseFailure as exc:
        c.log.warn(f"scoping failed ({exc.detail}); falling back to the "
                   "root-cause files")
        data = {}

    plan = _plan_from(data, root_cause, suspects)
    harden(ctx, plan, root_cause, candidates or [])
    return plan


def _plan_from(data: dict, root_cause, suspects: list[str]) -> ChangePlan:
    plan = ChangePlan(
        fix_description=str(data.get("fix_description", "")).strip()
        or f"Address the root cause: {root_cause.statement}",
        interface_changes=bool(data.get("interface_changes")),
        estimated_lines_changed=_int(data.get("estimated_lines_changed"), 10),
        fix_classification=str(data.get("fix_classification")
                               or "minimal_edit"),
    )
    for raw in (data.get("files_to_change") or [])[:8]:
        if isinstance(raw, dict) and raw.get("path"):
            plan.files_to_change.append(FileIntent(
                str(raw["path"]).lstrip("./"),
                (str(raw["symbol"]) if raw.get("symbol") else None),
                str(raw.get("intent", ""))[:200],
                is_new=bool(raw.get("new_file"))))
        elif isinstance(raw, str):
            plan.files_to_change.append(FileIntent(raw.lstrip("./")))
    plan.files_must_not_change = [str(p).lstrip("./") for p in
                                  (data.get("files_must_not_change") or [])[:20]]
    if not plan.files_to_change:
        plan.files_to_change = [FileIntent(p) for p in suspects[:1]]
    return plan


def _int(value, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _plausible_new_path(path: str) -> bool:
    """A new path must be relative, inside the repository, and source."""
    from ..repo.search import is_source
    if not path or path.startswith(("/", "~")) or ".." in Path(path).parts:
        return False
    if NEVER_TOUCH.search(path):
        return False
    return is_source(path)


def harden(ctx, plan: ChangePlan, root_cause,
           candidates: list | None = None) -> None:
    """Everything below is code, because a forgotten caller breaks the build."""
    c = ctx
    known = set(c.search.files())

    # 1. every allow-listed path must exist and be tracked
    kept, dropped = [], []
    for fi in plan.files_to_change:
        if fi.path in known:
            fi.is_new = False
            kept.append(fi)
        elif getattr(fi, "is_new", False) and _plausible_new_path(fi.path):
            # A fix that needs a new module is ordinary work. The path still
            # has to look like source in this repository, so a hallucinated
            # path cannot smuggle itself onto the allow list.
            kept.append(fi)
        else:
            matches = [f for f in known if f.endswith("/" + fi.path)]
            if len(matches) == 1:
                fi.path = matches[0]
                fi.is_new = False
                kept.append(fi)
            else:
                dropped.append(fi.path)
    if dropped:
        c.log.line(f"dropped {len(dropped)} path(s) that do not exist: "
                   + ", ".join(dropped[:3]))
    plan.files_to_change = kept or [
        FileIntent(f.path) for f in root_cause.files
        if f.path in known and not NEVER_TOUCH.search(f.path)][:1]

    # Last resort. An empty plan is not a cautious plan: P3 refuses it, the
    # cycle burns, and the run ends having changed nothing and explained
    # nothing. Everything below is deterministic and already computed, so
    # using it costs nothing and is strictly better than giving up.
    if not plan.files_to_change:
        fallback = [p for p in (candidates or [])
                    if p in known and not NEVER_TOUCH.search(p)
                    and is_source(p)]
        if fallback:
            c.log.line(f"plan named no usable file; falling back to "
                       f"{fallback[0]}")
            plan.files_to_change = [FileIntent(fallback[0])]
        else:
            c.degraded("no_target",
                       "nothing in the plan, the root cause or the "
                       "localization signals names a file in this repository")

    # A must-not-change entry that does not exist here is noise: it makes the
    # report look like the harness understands a repository it does not.
    phantom = [p for p in plan.files_must_not_change
               if p not in known and not p.endswith("/")]
    if phantom:
        plan.files_must_not_change = [p for p in plan.files_must_not_change
                                      if p not in phantom]
        c.log.line(f"dropped {len(phantom)} must-not-change path(s) that do "
                   f"not exist here")

    # 2. test / lock / generated / vendor paths are moved to the deny list
    moved = [fi.path for fi in plan.files_to_change
             if NEVER_TOUCH.search(fi.path)]
    if moved:
        plan.files_to_change = [fi for fi in plan.files_to_change
                                if fi.path not in moved]
        plan.files_must_not_change += moved
        c.log.line("moved to must-not-change (test/lock/generated): "
                   + ", ".join(moved[:4]))

    # 3. interface changes -> enumerate the actual call sites (FR-20)
    if not plan.files_to_change:
        plan.files_to_change = [
            FileIntent(f.path) for f in root_cause.files
            if f.path in known and not NEVER_TOUCH.search(f.path)][:1]
    if not plan.files_to_change:
        # Last resort: the harness's own ranked candidates. Proceeding with
        # no target at all would crash the implementer.
        plan.files_to_change = [
            FileIntent(p) for p in (candidates or [])
            if p in known and not NEVER_TOUCH.search(p)][:1]
    if not plan.files_to_change:
        c.log.warn("no source file to change after hardening")
        return
    for fi in plan.files_to_change:
        if not fi.symbol:
            fi.symbol = _symbol_at(c.repo, fi.path, root_cause)
    symbols_changed = [fi.symbol for fi in plan.files_to_change if fi.symbol]
    callers: list[str] = []
    for sym in symbols_changed:
        for fi in plan.files_to_change:
            callers += callers_of(c, sym, fi.path)
    plan.callers_requiring_update = sorted(set(callers))[:20]
    if plan.callers_requiring_update:
        plan.interface_changes = plan.interface_changes or len(
            plan.callers_requiring_update) > 0
        c.log.computed(
            "callers",
            f"{', '.join(symbols_changed[:2])}: "
            f"{len(plan.callers_requiring_update)} call site(s)",
            plain=f"callers of {', '.join(symbols_changed[:2])}: "
                  f"{len(plan.callers_requiring_update)} call site(s)")
        for site in plan.callers_requiring_update[:4]:
            c.log.cont(site)

    # 4. FR-19: kind is computed, never asserted
    plan.kind = ("cross_cutting"
                 if len(plan.files_to_change) > 1 or plan.callers_requiring_update
                 else "single_file")

    # 5. estimate sanity, clamped by conservative mode
    cap = 15 if c.cfg.conservative else 120
    if plan.estimated_lines_changed > cap:
        c.log.line(f"estimate {plan.estimated_lines_changed} exceeds the cap; "
                   f"clamping to {cap}")
        plan.estimated_lines_changed = cap
    if c.cfg.conservative and len(plan.files_to_change) > 1:
        plan.files_to_change = plan.files_to_change[:1]
        plan.kind = "single_file"

    try:
        plan.validate()
    except RecordError as exc:
        c.log.warn(f"plan invalid after hardening: {exc}")

    c.log.computed("plan", f"{plan.kind}, {len(plan.files_to_change)} "
                           f"file(s), ~{plan.estimated_lines_changed} lines",
                   plain=f"plan: {plan.kind}, {len(plan.files_to_change)} "
                         f"file(s), ~{plan.estimated_lines_changed} lines")
    c.log.cont(plan.fix_description[:200])


def _symbol_at(repo: Path, path: str, root_cause) -> str | None:
    for f in root_cause.files:
        if f.path == path and f.lines and f.lines[0]:
            try:
                for sym in symbols(repo / path):
                    if sym.start <= f.lines[0] <= sym.end and sym.kind != "class":
                        return sym.name
            except Exception:
                return None
    return None
