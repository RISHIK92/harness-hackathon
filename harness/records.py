"""Structured records exchanged between phases (SPEC.md 7).

Every record the harness acts on is schema-validated; a validation failure is
a typed, repairable event, never a silent pass.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

BUG_CLASSES = ("logic", "null", "type", "config", "external", "race",
               "missing_case")
TASK_TYPES = ("BUG_FIX", "FEATURE", "REFACTOR", "CONFIG", "DEPENDENCY",
              "UNKNOWN")
CONFIDENCE = ("low", "medium", "high")


class RecordError(ValueError):
    """A structured output failed validation."""


@dataclass
class Anchors:
    files: list = field(default_factory=list)
    symbols: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    frames: list = field(default_factory=list)     # (file, line, func)
    versions: list = field(default_factory=list)
    repro: list = field(default_factory=list)

    def coverage(self) -> float:
        present = sum(bool(x) for x in (self.files, self.symbols, self.errors,
                                        self.frames, self.repro))
        return present / 5.0

    def render(self) -> str:
        bits = []
        for name in ("files", "symbols", "errors", "frames", "versions",
                     "repro"):
            vals = getattr(self, name)
            if vals:
                bits.append(f"  {name}: " + ", ".join(str(v)[:80]
                                                      for v in vals[:6]))
        return "\n".join(bits) or "  (no anchors extracted)"


@dataclass
class IssueRecord:
    raw: str
    title: str = ""
    symptom: str = ""
    expected: str | None = None
    actual: str | None = None
    anchors: Anchors = field(default_factory=Anchors)
    vagueness: float = 0.0
    min_hypotheses: int = 2
    task_type: str = "BUG_FIX"

    def to_json(self) -> dict:
        d = asdict(self)
        d["raw"] = self.raw[:4000]
        return d


@dataclass
class Check:
    kind: str            # grep | read | test | git | version | env
    arg: str


@dataclass
class Hypothesis:
    id: str
    statement: str
    predicts: str = ""
    check: Check | None = None
    result: str | None = None
    support: str = "untested"       # confirmed|refuted|inconclusive|untested

    def render(self) -> str:
        line = f"{self.id} {self.statement}"
        if self.check:
            line += f"\n    check: {self.check.kind} {self.check.arg}"
        if self.result is not None:
            line += f"\n    result: {self.result[:200]}"
        line += f"\n    verdict: {self.support.upper()}"
        return line


@dataclass
class Evidence:
    source: str          # grep | read | git | test | probe | sbfl | bisect
    detail: str
    event_i: int = -1

    def render(self) -> str:
        return f"[{self.source}] {self.detail[:300]}"


@dataclass
class AffectedFile:
    path: str
    lines: tuple = (0, 0)
    why: str = ""


@dataclass
class RootCauseRecord:
    statement: str
    classification: str = "logic"
    files: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    confidence: str = "medium"
    external_factors: dict = field(default_factory=dict)
    hypotheses: list = field(default_factory=list)
    alternatives_rejected: list = field(default_factory=list)
    route: str = "FULL"

    def validate(self, existing_paths: set[str]) -> None:
        if not self.statement or len(self.statement.split()) < 4:
            raise RecordError("root cause statement is empty or too short")
        if self.classification not in BUG_CLASSES:
            raise RecordError(f"unknown classification {self.classification!r}")
        if self.confidence not in CONFIDENCE:
            raise RecordError(f"unknown confidence {self.confidence!r}")
        if not self.evidence:
            raise RecordError("root cause cites no evidence")
        if self.classification not in ("config", "external"):
            named = [f.path for f in self.files if f.path in existing_paths]
            if not named:
                raise RecordError(
                    "root cause names no existing file in the repository")

    def to_json(self) -> dict:
        return {
            "statement": self.statement,
            "classification": self.classification,
            "files": [asdict(f) for f in self.files],
            "evidence": [asdict(e) for e in self.evidence],
            "confidence": self.confidence,
            "external_factors": self.external_factors,
            "hypotheses": [_hyp_json(h) for h in self.hypotheses],
            "alternatives_rejected": self.alternatives_rejected,
            "route": self.route,
        }


def _hyp_json(h: Hypothesis) -> dict:
    return {"id": h.id, "statement": h.statement, "predicts": h.predicts,
            "check": asdict(h.check) if h.check else None,
            "result": (h.result or "")[:500], "support": h.support}


@dataclass
class FileIntent:
    path: str
    symbol: str | None = None
    intent: str = ""


@dataclass
class ChangePlan:
    fix_description: str = ""
    files_to_change: list = field(default_factory=list)
    files_must_not_change: list = field(default_factory=list)
    kind: str = "single_file"
    interface_changes: bool = False
    callers_requiring_update: list = field(default_factory=list)
    estimated_lines_changed: int = 10
    fix_classification: str = "minimal_edit"
    risks: list = field(default_factory=list)

    def validate(self) -> None:
        if not self.fix_description:
            raise RecordError("plan has no plain-English description")
        if not self.files_to_change:
            raise RecordError("plan changes no files")
        if self.estimated_lines_changed <= 0:
            raise RecordError("estimate must be positive")

    @property
    def allowed(self) -> set:
        return {f.path for f in self.files_to_change}

    @property
    def forbidden(self) -> set:
        return set(self.files_must_not_change)

    def to_json(self) -> dict:
        d = asdict(self)
        d["files_to_change"] = [asdict(f) for f in self.files_to_change]
        return d


@dataclass
class ConfidenceReport:
    root_cause_evidenced: bool = False
    fix_addresses_root_cause: bool = False
    existing_tests_pass: bool = False
    no_unintended_changes: bool = False
    diff_proportional: bool = False
    external_factors_resolved: bool = False
    judge_conclusive: bool = True
    blocking: list = field(default_factory=list)

    CONDITIONS = ("root_cause_evidenced", "fix_addresses_root_cause",
                  "existing_tests_pass", "no_unintended_changes",
                  "diff_proportional", "external_factors_resolved")
    HARD = ("existing_tests_pass", "no_unintended_changes",
            "diff_proportional")

    @property
    def score(self) -> int:
        return sum(bool(getattr(self, c)) for c in self.CONDITIONS)

    @property
    def overall(self) -> str:
        if self.score == 6:
            return "HIGH"
        if all(getattr(self, c) for c in self.HARD):
            return "MEDIUM"
        return "LOW"

    def render(self) -> str:
        marks = "  ".join(
            f"C{i+1} {'ok' if getattr(self, c) else 'FAIL'}"
            for i, c in enumerate(self.CONDITIONS))
        return f"{marks}   ({self.score}/6)"

    def to_json(self) -> dict:
        d = {c: bool(getattr(self, c)) for c in self.CONDITIONS}
        d.update(overall=self.overall, score=self.score,
                 blocking=self.blocking)
        return d
