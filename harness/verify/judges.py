"""Cheap-model judges (FR-31, FR-32, SPEC.md 9.6).

Both are small-context classification with all the evidence supplied, which
is what the cheapest model is reliable at.  G5 flags and never fixes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..structured import ParseFailure, extract_json

DIFF_PROMPT = """\
ROOT CAUSE IDENTIFIED: {root_cause}
FIX DESCRIPTION:       {description}
DIFF (hunks only):
{diff}

Does this diff address the stated root cause?
Answer on the first line exactly YES or NO.
On the second line, name the changed identifier or line that does the fixing.
On the third line, one sentence: how does that change stop the reported symptom?"""

PRACTICES_PROMPT = """\
LANGUAGE: {language}
FUNCTION AS MODIFIED:
{code}

Does this function have an immediately visible problem with:
null/undefined handling, error handling consistent with the surrounding code,
off-by-one, variable shadowing, or anything else obviously wrong?

Answer PASS or FLAG on the first line. If FLAG, one sentence naming the issue."""


@dataclass
class DiffVerdict:
    addresses: bool = False
    conclusive: bool = True
    mechanism: str = ""
    reason: str = ""
    skipped: bool = False

    def render(self) -> str:
        if self.skipped:
            return "diff sanity: skipped (budget)"
        verdict = "YES" if self.addresses else "NO"
        if not self.conclusive:
            verdict = "INCONCLUSIVE"
        return f"diff sanity: {verdict} - {self.reason[:120]}"


@dataclass
class PracticesVerdict:
    issues: list = field(default_factory=list)
    skipped: bool = False

    def render(self) -> str:
        if self.skipped:
            return "best practices: skipped (budget)"
        if not self.issues:
            return "best practices: no flags"
        return "best practices: " + "; ".join(self.issues[:4])


def diff_sanity(ctx, root_cause, plan, diff: str) -> DiffVerdict:
    """FR-31, on the cheapest model. A negative verdict re-opens Phase 1."""
    if not ctx.budget_ok("P4"):
        return DiffVerdict(addresses=True, skipped=True)

    hunks = _hunks_only(diff)
    messages = [
        {"role": "system", "content":
         "You check whether a diff addresses a stated root cause. "
         "Answer exactly as instructed. Do not speculate."},
        {"role": "user", "content": DIFF_PROMPT.format(
            root_cause=root_cause.statement,
            description=plan.fix_description, diff=hunks[:6000])},
    ]
    reply = ctx.router.call("p4_judge_diff", messages, "P4", max_tokens=300)
    text = (reply.text or "").strip()

    verdict = DiffVerdict()
    first = text.splitlines()[0].strip().upper() if text else ""
    verdict.addresses = first.startswith("YES")
    lines = text.splitlines()
    verdict.mechanism = lines[1].strip() if len(lines) > 1 else ""
    verdict.reason = lines[2].strip() if len(lines) > 2 else text[:160]

    # Anti-sycophancy: a YES whose stated mechanism names nothing that appears
    # in the diff is not evidence. Downgrade it to advisory.
    if verdict.addresses and not _mentions_diff_identifier(verdict.mechanism,
                                                           diff):
        verdict.conclusive = False
        verdict.reason = ("could not name a changed identifier; treated as "
                          "inconclusive")
    ctx.events.append("judge", "P4",
                      {"kind": "diff_sanity", "verdict": verdict.addresses,
                       "conclusive": verdict.conclusive,
                       "mechanism": verdict.mechanism})
    return verdict


IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _mentions_diff_identifier(mechanism: str, diff: str) -> bool:
    if not mechanism:
        return False
    changed = set()
    for line in diff.splitlines():
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            changed.update(IDENT.findall(line))
    return bool(changed & set(IDENT.findall(mechanism)))


def _hunks_only(diff: str) -> str:
    keep = []
    for line in diff.splitlines():
        if line.startswith(("diff --git", "index ", "--- ", "+++ ")):
            continue
        keep.append(line)
    return "\n".join(keep)


def best_practices(ctx, functions: list) -> PracticesVerdict:
    """FR-32: per function, on the cheapest model, FLAG ONLY -- never fixes."""
    if not ctx.budget_ok("P4") or not functions:
        return PracticesVerdict(skipped=not functions)

    out = PracticesVerdict()
    for path, name, code in functions[:4]:
        messages = [
            {"role": "system", "content":
             "You review one function. Answer PASS or FLAG only."},
            {"role": "user", "content": PRACTICES_PROMPT.format(
                language=ctx.toolchain.language if ctx.toolchain else "unknown",
                code=code[:3000])},
        ]
        reply = ctx.router.call("p4_judge_practices", messages, "P4",
                                max_tokens=200)
        text = (reply.text or "").strip()
        if text.upper().startswith("FLAG"):
            detail = " ".join(text.splitlines()[1:])[:160] or text[:160]
            out.issues.append(f"{path}::{name}: {detail}")
    ctx.events.append("judge", "P4",
                      {"kind": "best_practices", "issues": out.issues})
    return out
