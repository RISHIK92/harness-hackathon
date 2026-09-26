"""New vs pre-existing vs fixed vs flaky (FR-29, FR-30, SPEC.md 9.5).

Without the flake rerun, one flaky test consumes the entire cycle budget.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import parse_results as P

NEW = "new_failure"
PRE = "pre_existing"
FIXED = "fixed"
UNKNOWN = "unknown_failure"
FLAKY = "flaky"


@dataclass
class Classification:
    new: list = field(default_factory=list)
    pre_existing: list = field(default_factory=list)
    fixed: list = field(default_factory=list)
    unknown: list = field(default_factory=list)
    flaky: list = field(default_factory=list)
    collection_error: bool = False

    @property
    def blocking(self) -> list:
        """Failures we own. Flakes are excluded by construction."""
        return sorted(set(self.new + self.unknown) - set(self.flaky))

    @property
    def clean(self) -> bool:
        return not self.blocking and not self.collection_error

    def to_json(self) -> dict:
        return {"new": self.new, "pre_existing": self.pre_existing,
                "fixed": self.fixed, "unknown": self.unknown,
                "flaky": self.flaky, "blocking": self.blocking,
                "collection_error": self.collection_error}


def classify(baseline_tests: dict, current: P.TestResults) -> Classification:
    c = Classification(collection_error=current.collection_error)
    for tid, status in current.tests.items():
        was = baseline_tests.get(tid)
        if status in P.FAILING and was in P.PASSING:
            c.new.append(tid)
        elif status in P.FAILING and was in P.FAILING:
            c.pre_existing.append(tid)
        elif status in P.PASSING and was in P.FAILING:
            c.fixed.append(tid)
        elif status in P.FAILING and was is None:
            c.unknown.append(tid)
    for lst in (c.new, c.pre_existing, c.fixed, c.unknown):
        lst.sort()
    return c


def rerun_for_flakes(cls: Classification, rerun, log) -> Classification:
    """Rerun each suspect failure once in isolation. `rerun(tid) -> bool`."""
    suspects = sorted(set(cls.new + cls.unknown))
    for tid in suspects:
        try:
            still_failing = rerun(tid)
        except Exception as exc:              # a rerun must never end the run
            log.debug(f"flake rerun failed for {tid}: {exc}")
            continue
        if not still_failing:
            cls.flaky.append(tid)
            log.note("flaky", tid)
    cls.flaky.sort()
    return cls
