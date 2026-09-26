"""The failing test as the specification (SPEC.md 21.2).

When a baseline-failing test is plainly about the issue, hypothesis
elimination is unnecessary: there is an oracle, and its verdict is ground
truth rather than a proxy for it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..verify import parse_results as P


@dataclass
class Oracle:
    test_id: str = ""
    reason: str = ""

    def __bool__(self) -> bool:
        return bool(self.test_id)


def _tokens(text: str) -> set[str]:
    """Both the underscore-joined form and its parts.

    A test id like `test_grand_total_rejects_negative_discount` must match the
    symbol `grand_total` from the issue, so splitting on underscores as well
    as on punctuation is required.
    """
    out: set[str] = set()
    for chunk in re.split(r"[^A-Za-z0-9_]+", text or ""):
        if len(chunk) > 2:
            out.add(chunk.lower())
        for part in chunk.split("_"):
            if len(part) > 2:
                out.add(part.lower())
    return out


def find(baseline, issue) -> Oracle:
    """Pick a baseline-failing test the issue is plainly about."""
    failing = [t for t, s in baseline.tests.items() if s in P.FAILING]
    if not failing:
        return Oracle()

    symbol_tokens: set[str] = set()
    for sym in issue.anchors.symbols:
        name = sym.split(".")[-1].lower()
        symbol_tokens.add(name)
        symbol_tokens.update(p for p in name.split("_") if len(p) > 2)
    file_stems = {p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
                  for p in issue.anchors.files}
    issue_tokens = _tokens(issue.raw)

    best, best_score, why = "", 0, ""
    for tid in failing:
        tid_tokens = _tokens(tid)
        score = 0
        reasons = []
        hit = tid_tokens & symbol_tokens
        if hit:
            score += 3 if any("_" in h for h in hit) else 2
            reasons.append(f"test name mentions {sorted(hit)[0]}")
        hit = tid_tokens & file_stems
        if hit:
            score += 2
            reasons.append(f"same module as {sorted(hit)[0]}")
        overlap = tid_tokens & issue_tokens - {"test", "tests", "py"}
        if len(overlap) >= 2:
            score += 1
            reasons.append("shares wording with the issue")
        detail = (baseline.failures.get(tid) or "").lower()
        for err in issue.anchors.errors:
            head = err.strip().split(":")[0].lower()
            if head and len(head) > 4 and head in detail:
                score += 3
                reasons.append(f"fails with the reported {head}")
                break
        if score > best_score:
            best, best_score, why = tid, score, "; ".join(reasons)

    if best_score >= 3:
        return Oracle(test_id=best, reason=why)
    if len(failing) == 1 and best_score >= 1:
        return Oracle(test_id=failing[0], reason=why or "the only failing test")
    return Oracle()
