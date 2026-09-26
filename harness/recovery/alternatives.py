"""Rejected alternatives as the recovery branch (SPEC.md 29).

Part I stored `alternatives_rejected` and never read it.  By cycle three the
leading hypothesis has demonstrably failed, and the second-best one is
already generated and already partly evidenced -- re-entering P1 with a
DIFFERENT hypothesis is cheaper and more likely to work than re-prompting the
same one with different words.
"""
from __future__ import annotations

SUPPORT_RANK = {"confirmed": 3, "inconclusive": 2, "untested": 1,
                "refuted": 0}


def next_alternative(root_cause, tried: set):
    """The best untried, unrefuted hypothesis, or None."""
    candidates = [h for h in (root_cause.hypotheses or [])
                  if h.id not in tried and h.support != "refuted"]
    if not candidates:
        return None
    return max(candidates, key=lambda h: (SUPPORT_RANK.get(h.support, 0), h.id))


def describe(hypothesis) -> str:
    return (f"{hypothesis.id}: {hypothesis.statement} "
            f"(check {hypothesis.check.kind} {hypothesis.check.arg[:40]} "
            f"-> {hypothesis.support})")
