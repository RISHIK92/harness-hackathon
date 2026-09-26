"""Per-run metrics (SPEC.md 33.1).

These are what let an architectural change be judged instead of argued about.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class RunMetrics:
    fixture: str
    model_tier: str = "T1"
    resolved: bool = False
    route: str = ""
    task_type: str = ""
    oracle: bool = False
    new_failures: int = 0
    pre_existing: int = 0
    fixed: int = 0
    flaky: int = 0
    cycles: int = 0
    diff_lines: int = 0
    reference_diff_lines: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    wall_s: float = 0.0
    model_calls: int = 0
    exit_code: int = 0
    confidence: str = ""
    hygiene_flags: int = 0
    notes: list = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


def append(path: Path, metrics: RunMetrics) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(metrics.to_json(), default=str) + "\n")


def summarize(path: Path) -> dict:
    rows = []
    for line in Path(path).read_text("utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    if not rows:
        return {}
    by_tier: dict = {}
    for r in rows:
        slot = by_tier.setdefault(r["model_tier"], {"n": 0, "resolved": 0,
                                                    "tokens": 0, "cycles": 0})
        slot["n"] += 1
        slot["resolved"] += int(r["resolved"])
        slot["tokens"] += r["tokens_in"] + r["tokens_out"]
        slot["cycles"] += r["cycles"]
    return by_tier
