"""Append-only event log -- the harness's memory and audit trail (SPEC.md 6.1).

Every artifact is derived from this file, which makes replay (SPEC.md 30.2)
and post-mortems free.  Payloads over BLOB_THRESHOLD are written to blobs and
referenced, so the log itself stays greppable.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..logging_ui import redact

BLOB_THRESHOLD = 4096

KINDS = (
    "phase_start", "phase_end", "model_request", "model_reply",
    "tool_call", "tool_result", "edit_applied", "test_run", "judge",
    "degradation", "checkpoint", "condensation",
    # Setting up the working copy, writing a test of our own, and the one
    # question the harness is allowed to ask.
    "dependencies", "repro_written", "repro", "clarified",
)


@dataclass
class Event:
    i: int
    t: float
    kind: str
    phase: str
    payload: dict = field(default_factory=dict)
    summary: str = ""
    tokens_est: int = 0
    payload_ref: str | None = None

    def to_json(self) -> dict:
        d = asdict(self)
        if d["payload_ref"] is None:
            d.pop("payload_ref")
        return d


class EventLog:
    """Append-only JSONL log with out-of-line blobs."""

    def __init__(self, run_dir: Path, secrets: list[str] | None = None) -> None:
        self.run_dir = Path(run_dir)
        self.blob_dir = self.run_dir / "blobs"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.blob_dir.mkdir(exist_ok=True)
        self.path = self.run_dir / "trajectory.jsonl"
        self.secrets = [s for s in (secrets or []) if s]
        self._i = 0
        self._events: list[Event] = []

    # -- writing -----------------------------------------------------------
    def append(self, kind: str, phase: str, payload: dict | None = None,
               summary: str = "", tokens_est: int = 0) -> Event:
        if kind not in KINDS:
            raise ValueError(f"unknown event kind: {kind}")
        payload = payload or {}
        ev = Event(i=self._i, t=time.time(), kind=kind, phase=phase,
                   summary=summary, tokens_est=tokens_est)
        self._i += 1

        blob = json.dumps(payload, default=str)
        if len(blob) > BLOB_THRESHOLD:
            ref = f"blobs/{ev.i:05d}.json"
            (self.run_dir / ref).write_text(
                redact(blob, self.secrets), encoding="utf-8")
            ev.payload_ref = ref
            ev.payload = {"_elided": True, "bytes": len(blob)}
        else:
            ev.payload = json.loads(redact(blob, self.secrets))

        self._events.append(ev)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev.to_json(), default=str) + "\n")
        return ev

    # -- reading -----------------------------------------------------------
    def load_payload(self, ev: Event) -> dict:
        if ev.payload_ref:
            return json.loads((self.run_dir / ev.payload_ref).read_text("utf-8"))
        return ev.payload

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    def by_kind(self, *kinds: str) -> list[Event]:
        return [e for e in self._events if e.kind in kinds]

    def by_phase(self, phase: str) -> list[Event]:
        return [e for e in self._events if e.phase == phase]

    def last(self, n: int) -> list[Event]:
        return self._events[-n:] if n else []

    @classmethod
    def read(cls, path: Path) -> list[Event]:
        """Load a recorded trajectory (for replay)."""
        out: list[Event] = []
        for raw in Path(path).read_text("utf-8").splitlines():
            if not raw.strip():
                continue
            d = json.loads(raw)
            out.append(Event(
                i=d["i"], t=d["t"], kind=d["kind"], phase=d.get("phase", ""),
                payload=d.get("payload", {}), summary=d.get("summary", ""),
                tokens_est=d.get("tokens_est", 0),
                payload_ref=d.get("payload_ref"),
            ))
        return out
