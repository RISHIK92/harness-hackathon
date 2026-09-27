"""Supervising a call instead of guessing its budget up front (SPEC.md 40).

Every output budget in this harness was a number somebody picked: 3000
tokens for an edit, 300 for a judge. Those were sized against models that
answer immediately. A reasoning model spends its output budget thinking
first, so on a real run against `deepseek-v4-pro` every implement call came
back the same way:

    p3_implement  1.2k -> 3.0k  55.7s   -> empty reply
    p3_implement  1.2k -> 3.0k  51.5s   -> empty reply
    p3_implement  1.2k -> 3.0k  replayed -> empty reply

`-> 3.0k` is the ceiling, every time. The model was cut off mid-thought, the
strip left nothing, and the cache then re-served that same truncated failure
to the next cycle. Four cycles, 36k tokens, no patch -- and the model had
diagnosed the bug correctly in P1.

The fix is not a bigger constant. A bigger constant is the same mistake with
a different number, and it would waste the budget on models that do not need
it. What is needed is to look at what came back and decide:

  * truncated at the ceiling -> the answer was never reached. Raise the
    ceiling and ask again, bypassing the cache, because re-serving a
    truncated reply cannot produce a different outcome.
  * answered normally -> leave the budget alone.
  * truncated repeatedly even when raised -> stop. The model is not going to
    finish, and further attempts are the rogue loop this exists to prevent.

Deliberately not a model call. This is a decision about evidence already in
hand -- a stop reason and a token count -- and spending a model call to
interpret them would be slower, dearer and less reliable than reading them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# How far a budget may be raised, and how often. Two attempts covers a model
# that thinks for a while; beyond that the problem is not the ceiling.
GROWTH = 3.0
MAX_ATTEMPTS = 3
CEILING = 32_000


def _enabled() -> bool:
    raw = (os.environ.get("HARNESS_ADAPTIVE_TOKENS") or "").strip().lower()
    return raw not in ("0", "off", "no", "false", "never")


# How far a size estimate may be revised upward, and after how many
# consistent rejections. Two independent attempts agreeing that the fix is
# bigger than the guess is evidence about the fix, not about the model.
SIZE_EVIDENCE = 2
SIZE_GROWTH = 2.5


@dataclass
class SizeWatcher:
    """Revises a scope estimate that the work keeps disagreeing with.

    `estimated_lines_changed` is a guess made before the code was read. When
    every attempt lands at 21 lines against an estimate of 10, the estimate
    is what is wrong -- but the gate rejected all five cycles and told the
    model to "re-scope with a tighter estimate", which is the opposite of
    what the evidence said.

    Raised only on repeated agreement, and only once, so a genuinely
    oversized change is still refused.
    """
    log: object = None
    seen: list = field(default_factory=list)
    raised: bool = False

    def observe(self, actual: int, estimate: int) -> int | None:
        """Record a rejected size; return a new estimate when warranted."""
        self.seen.append(actual)
        if self.raised or len(self.seen) < SIZE_EVIDENCE:
            return None
        recent = self.seen[-SIZE_EVIDENCE:]
        # They have to agree: wildly different sizes mean the model is
        # flailing, not that the estimate is too small.
        if max(recent) > min(recent) * 1.5:
            return None
        ceiling = int(estimate * SIZE_GROWTH)
        revised = min(max(recent), ceiling)
        if revised <= estimate:
            return None
        self.raised = True
        if self.log is not None:
            self.log.note("scope",
                          f"{len(recent)} attempts agreed the fix needs "
                          f"~{max(recent)} lines against an estimate of "
                          f"{estimate}; raising it to {revised}")
        return revised


@dataclass
class Attempt:
    """One observation of a call that did not finish."""
    role: str
    asked: int
    granted: int
    stop_reason: str


@dataclass
class Watcher:
    """Decides whether a truncated call deserves another, larger attempt."""
    log: object = None
    attempts: list = field(default_factory=list)
    granted: dict = field(default_factory=dict)      # role -> last budget

    def truncated(self, reply) -> bool:
        """Did the model run out of room before it finished?"""
        if reply is None:
            return False
        if (getattr(reply, "stop_reason", "") or "").lower() in (
                "length", "max_tokens", "max_output_tokens"):
            return True
        # Some gateways report nothing useful; an empty answer that consumed
        # a full budget is the same event by another name.
        text = (getattr(reply, "text", "") or "").strip()
        out = getattr(reply, "tokens_out", 0) or 0
        asked = self.granted.get("__last__", 0)
        return bool(not text and asked and out >= asked * 0.9)

    def next_budget(self, role: str, asked: int) -> int | None:
        """A larger budget for `role`, or None when it has had enough."""
        if not _enabled():
            return None
        tried = [a for a in self.attempts if a.role == role]
        if len(tried) >= MAX_ATTEMPTS:
            return None
        nxt = min(int(asked * GROWTH), CEILING)
        return nxt if nxt > asked else None

    def observe(self, role: str, asked: int, reply) -> int | None:
        """Record the outcome; return a retry budget when one is warranted."""
        self.granted["__last__"] = asked
        self.granted[role] = asked
        if not self.truncated(reply):
            return None

        nxt = self.next_budget(role, asked)
        self.attempts.append(Attempt(role=role, asked=asked, granted=nxt or 0,
                                     stop_reason=getattr(reply, "stop_reason",
                                                         "") or "length"))
        if self.log is not None:
            if nxt:
                self.log.note("budget",
                              f"{role} was cut off at {asked} output tokens; "
                              f"retrying with {nxt}")
            else:
                self.log.degraded(
                    "truncated",
                    f"{role} kept running out of output room after "
                    f"{MAX_ATTEMPTS} attempts; the model is not finishing")
        return nxt

    def summary(self) -> dict:
        return {"truncations": len(self.attempts),
                "roles": sorted({a.role for a in self.attempts})}
