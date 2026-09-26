"""Convergence router and self-consistency confidence (SPEC.md 21.5).

Part I only ever degrades when uncertain; it never accelerates when certain.
This closes that, and it measures localization confidence by AGREEMENT rather
than by asking the model how sure it is -- models are badly calibrated when
asked directly and well calibrated when sampled.

Localization is sampled rather than patches because localization errors are
unrecoverable and patch errors are caught by tests.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

ORACLE, DIRECT, FULL = "ORACLE", "DIRECT", "FULL"

BUDGET_SHARE = {ORACLE: 0.08, DIRECT: 0.18, FULL: 0.35}


@dataclass
class Signals:
    sbfl: list = field(default_factory=list)
    lexical: list = field(default_factory=list)
    structural: list = field(default_factory=list)
    historical: list = field(default_factory=list)

    def tops(self) -> dict:
        return {name: lst[0] for name, lst in
                (("sbfl", self.sbfl), ("lexical", self.lexical),
                 ("structural", self.structural),
                 ("historical", self.historical)) if lst}

    def merged(self, limit: int = 8) -> list:
        """Borda-style merge: rank across signals, best first."""
        score: dict[str, float] = {}
        for lst in (self.sbfl, self.lexical, self.structural, self.historical):
            for i, path in enumerate(lst[:limit]):
                score[path] = score.get(path, 0.0) + 1.0 / (i + 1)
        return [p for p, _ in sorted(score.items(),
                                     key=lambda kv: (-kv[1], kv[0]))][:limit]


@dataclass
class Route:
    name: str = FULL
    agreement: int = 0
    winner: str = ""
    reason: str = ""
    candidates: list = field(default_factory=list)

    @property
    def budget_share(self) -> float:
        return BUDGET_SHARE[self.name]

    def render(self) -> str:
        return f"route {self.name}  ({self.reason})"


def route(signals: Signals, oracle=None, forced: str | None = None) -> Route:
    candidates = signals.merged()
    if forced:
        return Route(name=forced, reason="forced by HARNESS_ROUTE",
                     candidates=candidates)
    if oracle:
        return Route(name=ORACLE, winner=getattr(oracle, "test_id", ""),
                     reason=f"oracle test: {getattr(oracle, 'reason', '')}",
                     candidates=candidates)

    tops = signals.tops()
    if not tops:
        return Route(name=FULL, reason="no localization signal",
                     candidates=candidates)

    counts = Counter(tops.values())
    winner, n = counts.most_common(1)[0]
    agreeing = sorted(k for k, v in tops.items() if v == winner)
    if n >= 2:
        return Route(name=DIRECT, agreement=n, winner=winner,
                     reason=f"{n} signals agree on {winner} "
                            f"({', '.join(agreeing)})",
                     candidates=candidates)
    return Route(name=FULL, agreement=1,
                 reason=f"signals disagree ({len(tops)} distinct tops)",
                 candidates=candidates)


def self_consistency(answers: list[str]) -> tuple[str, str, float]:
    """(modal answer, confidence band, share) from n sampled replies."""
    cleaned = [a.strip() for a in answers if a and a.strip()]
    if not cleaned:
        return "", "low", 0.0
    counts = Counter(cleaned)
    answer, n = counts.most_common(1)[0]
    share = n / len(cleaned)
    band = "high" if share >= 0.99 else "medium" if share >= 0.6 else "low"
    return answer, band, round(share, 2)
