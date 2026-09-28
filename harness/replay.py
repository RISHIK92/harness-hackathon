"""Deterministic replay, wired to `make test` (SPEC.md 30.2, NFR-5).

This turns reproducibility from a paragraph the evaluator has to believe into
a command they can run.  It requires NO API key: every model reply is served
from the call cache recorded during the run.

    python -m harness.replay [--trajectory PATH] [--repo PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from .context.events import EventLog
from .logging_ui import Logger
from .verify.runner import run


@dataclass
class Step:
    i: int
    kind: str
    label: str
    ok: bool
    detail: str = ""


def cache_key(model: str, messages: list, params: dict) -> str:
    blob = json.dumps([model, messages, params], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def find_trajectory(repo: Path) -> tuple[Path, Path] | tuple[None, None]:
    """(trajectory, target repo). Falls back to the last-run pointer."""
    candidate = repo / ".harness" / "run" / "trajectory.jsonl"
    if candidate.is_file():
        return candidate, repo

    pointer = repo / ".harness" / "last_run.json"
    if pointer.is_file():
        try:
            d = json.loads(pointer.read_text("utf-8"))
            traj, target = Path(d["trajectory"]), Path(d["repo"])
            if traj.is_file():
                return traj, target
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    return None, None


def replay(trajectory: Path, repo: Path, log: Logger) -> tuple[list, bool]:
    events = EventLog.read(trajectory)
    cache_dir = repo / ".harness" / "cache" / "calls"
    run_dir = trajectory.parent
    steps: list[Step] = []

    for ev in events:
        if ev.kind == "model_request":
            payload = _payload(ev, run_dir)
            model = payload.get("model", "")
            key = cache_key(model, payload.get("messages", []),
                            payload.get("params", {}))
            cached = (cache_dir / f"{key}.json").is_file()
            steps.append(Step(ev.i, "model", payload.get("label")
                              or model, cached,
                              "" if cached else "no cached reply"))

        elif ev.kind == "test_run":
            payload = _payload(ev, run_dir)
            cmd = payload.get("cmd", "")
            if not cmd:
                continue
            # A test command the harness itself recorded, re-run from the
            # operator's own trajectory file; model text in it was quoted
            # when it was built. Deny list off, as it was the first time.
            result = run(cmd, repo, timeout=300, check_deny=False)
            expected = payload.get("counts") or {}
            steps.append(Step(ev.i, "test", _label(cmd),
                              result.exit_code in (0, 1),
                              f"exit {result.exit_code}"))

        elif ev.kind == "edit_applied":
            payload = _payload(ev, run_dir)
            files = payload.get("files", [])
            ok = all((repo / f).is_file() for f in files)
            steps.append(Step(ev.i, "edit", ", ".join(files)[:48], ok))

    passed = sum(1 for s in steps if s.ok)
    log.raw("")
    log.rule("REPLAY")
    log.raw(f"trajectory      {trajectory}")
    log.raw(f"steps           {len(steps)}   passed {passed}   "
            f"failed {len(steps) - passed}")
    log.raw("")
    for s in steps:
        mark = "ok  " if s.ok else "FAIL"
        log.raw(f"  {mark} [{s.kind:<5}] {s.label}"
                + (f"   {s.detail}" if s.detail else ""))
    log.rule()
    return steps, passed == len(steps) and bool(steps)


def _label(cmd: str) -> str:
    """A readable name for a long, absolute-path test command."""
    parts = [p for p in cmd.split() if not p.startswith("--junitxml")]
    if parts and "/" in parts[0]:
        parts[0] = Path(parts[0]).name
    return " ".join(parts)[:60]


def _payload(ev, run_dir: Path) -> dict:
    if ev.payload_ref:
        try:
            return json.loads((run_dir / ev.payload_ref).read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return ev.payload or {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness.replay")
    parser.add_argument("--trajectory")
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    if args.trajectory:
        path, target = Path(args.trajectory), repo
    else:
        path, target = find_trajectory(repo)
        target = target or repo
    log = Logger()
    if not path or not path.is_file():
        log.raw("replay: no recorded run found "
                "(.harness/run/trajectory.jsonl)")
        return 2                       # `make test` falls through to pytest
    steps, ok = replay(path, target, log)
    if ok:
        log.raw("replay: every recorded step reproduced, with no API key")
        return 0
    log.raw("replay: the recorded run did NOT reproduce")
    return 1


if __name__ == "__main__":
    sys.exit(main())
