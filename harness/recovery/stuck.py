"""Stuck detection (SPEC.md 28).

Six signals, all computed from the event log with no model call.  On trigger
the orchestrator does NOT retry -- it escalates a level.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field

SAME_COMMAND = 3
SAME_FAILURE = 3
REVERTED_FILE = 2
SAME_DIFF = 2
NO_NEW_OBSERVATION = 4

ESCALATION = {"P3": "P2", "P2": "P1", "P1": "P1"}


@dataclass
class StuckState:
    commands: Counter = field(default_factory=Counter)
    failures: Counter = field(default_factory=Counter)
    diffs: Counter = field(default_factory=Counter)
    reverts: Counter = field(default_factory=Counter)
    seen: set = field(default_factory=set)
    barren_steps: int = 0
    triggers: list = field(default_factory=list)

    # -- observations ------------------------------------------------------
    def command(self, cmd: str, output: str) -> None:
        self.commands[_h(cmd + "|" + (output or "")[:400])] += 1

    def failure(self, signature: str) -> None:
        self.failures[_norm_failure(signature)] += 1

    def diff(self, text: str) -> None:
        self.diffs[_h(_norm_diff(text))] += 1

    def revert(self, path: str) -> None:
        self.reverts[path] += 1

    def observe(self, items) -> None:
        """New files, symbols or test ids seen this step."""
        fresh = {str(i) for i in items} - self.seen
        self.seen |= fresh
        self.barren_steps = 0 if fresh else self.barren_steps + 1

    # -- verdict -----------------------------------------------------------
    def check(self) -> str | None:
        for counter, limit, label in (
                (self.commands, SAME_COMMAND, "same command and output"),
                (self.failures, SAME_FAILURE, "same failure signature"),
                (self.diffs, SAME_DIFF, "identical diff already rejected"),
                (self.reverts, REVERTED_FILE, "file edited and reverted"),
        ):
            if counter and counter.most_common(1)[0][1] >= limit:
                return label
        if self.barren_steps >= NO_NEW_OBSERVATION:
            return "no new file, symbol or test observed"
        return None

    def fire(self, label: str) -> None:
        self.triggers.append(label)


def escalate(phase: str) -> str:
    return ESCALATION.get(phase, "P1")


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def _norm_failure(sig: str) -> str:
    """Drop line numbers and addresses so the same failure hashes the same."""
    s = re.sub(r"0x[0-9a-f]+", "0xADDR", sig or "")
    s = re.sub(r":\d+", ":N", s)
    return _h(s[:400])


def _norm_diff(text: str) -> str:
    keep = []
    for line in (text or "").splitlines():
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            keep.append(re.sub(r"\s+", " ", line).strip())
    return "\n".join(sorted(keep))
