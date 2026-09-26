"""Token, wall-clock and step budgets (SPEC.md 10.3, NFR-1).

Budget exhaustion is a typed exception the orchestrator converts into a
phase-specific degraded completion -- never a crash.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    def __init__(self, kind: str, phase: str, used: float, cap: float) -> None:
        super().__init__(f"{kind} budget exceeded in {phase}: {used:.0f}/{cap:.0f}")
        self.kind = kind
        self.phase = phase
        self.used = used
        self.cap = cap


@dataclass
class TokenBudget:
    total: int
    reserve_frac: float = 0.12
    used_in: int = 0
    used_out: int = 0
    used_cached: int = 0
    per_phase: dict = field(default_factory=dict)

    @property
    def used(self) -> int:
        return self.used_in + self.used_out

    @property
    def spendable(self) -> int:
        return int(self.total * (1 - self.reserve_frac))

    @property
    def remaining_frac(self) -> float:
        return max(0.0, 1.0 - self.used / max(1, self.spendable))

    def charge(self, phase: str, tin: int, tout: int, cached: int = 0) -> None:
        self.used_in += tin
        self.used_out += tout
        self.used_cached += cached
        slot = self.per_phase.setdefault(phase, {"in": 0, "out": 0, "cached": 0,
                                                 "calls": 0})
        slot["in"] += tin
        slot["out"] += tout
        slot["cached"] += cached
        slot["calls"] += 1

    def check(self, phase: str, cap: int) -> None:
        spent = self.per_phase.get(phase, {})
        used = spent.get("in", 0) + spent.get("out", 0)
        if used >= cap:
            raise BudgetExceeded("token", phase, used, cap)
        if self.used >= self.spendable:
            raise BudgetExceeded("token", phase, self.used, self.spendable)

    def cache_ratio(self) -> float:
        denom = self.used_in + self.used_cached
        return self.used_cached / denom if denom else 0.0


@dataclass
class WallClock:
    limit_s: float
    started: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started

    @property
    def remaining(self) -> float:
        return max(0.0, self.limit_s - self.elapsed)

    def check(self, phase: str) -> None:
        if self.elapsed >= self.limit_s:
            raise BudgetExceeded("wall-clock", phase, self.elapsed, self.limit_s)

    def would_exceed(self, secs: float) -> bool:
        return self.elapsed + secs > self.limit_s


@dataclass
class StepCounter:
    caps: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)

    def cap(self, phase: str, n: int) -> None:
        self.caps[phase] = n

    def step(self, phase: str) -> int:
        self.counts[phase] = self.counts.get(phase, 0) + 1
        cap = self.caps.get(phase)
        if cap is not None and self.counts[phase] > cap:
            raise BudgetExceeded("step", phase, self.counts[phase], cap)
        return self.counts[phase]

    def used(self, phase: str) -> int:
        return self.counts.get(phase, 0)


@dataclass
class Budgets:
    """The three budgets, travelling together."""
    tokens: TokenBudget
    clock: WallClock
    steps: StepCounter

    @classmethod
    def from_config(cls, cfg) -> "Budgets":
        return cls(
            tokens=TokenBudget(total=cfg.token_budget),
            clock=WallClock(limit_s=cfg.time_budget),
            steps=StepCounter(),
        )

    def check(self, phase: str, cfg) -> None:
        self.clock.check(phase)
        self.tokens.check(phase, cfg.phase_budget(phase))
