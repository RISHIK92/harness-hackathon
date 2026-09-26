"""Shared phase context: the handles every phase needs, and nothing more."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PhaseContext:
    cfg: object
    log: object
    router: object
    repo: Path
    search: object
    workspace: object
    toolchain: object
    events: object
    budgets: object
    external: object = None
    caps: object = None
    assembler: object = None
    notes: list = field(default_factory=list)

    def budget_ok(self, phase: str) -> bool:
        try:
            self.budgets.check(phase, self.cfg)
            return True
        except Exception:
            return False

    def degraded(self, tag: str, detail: str = "") -> None:
        self.log.degraded(tag, detail)
        self.notes.append(tag)
        try:
            self.events.append("degradation", "--", {"tag": tag,
                                                     "detail": detail})
        except Exception:
            pass
