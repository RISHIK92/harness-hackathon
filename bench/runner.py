"""Run the fixture matrix and record metrics (SPEC.md 33).

    python bench/runner.py                 # all fixtures, mock model
    python bench/runner.py --tier T0       # force a tier profile
    python bench/runner.py --only py-vague

With no AI_API_KEY the scripted stand-in in tests/mock_model.py is used, so
the matrix runs offline and deterministically.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from bench import evaluator, metrics            # noqa: E402
from harness import config as C                 # noqa: E402
from harness.logging_ui import Logger           # noqa: E402
from harness.orchestrator import Orchestrator   # noqa: E402

PYEXE = str((ROOT / ".venv" / "bin" / "python").resolve())
if not Path(PYEXE).exists():
    PYEXE = sys.executable


class _Patcher:
    def setattr(self, obj, name, value):
        setattr(obj, name, value)


def load_task(path: Path) -> dict:
    """A deliberately small YAML subset -- no dependency for one file format."""
    task: dict = {"expected": {}, "success": []}
    section = None
    issue: list = []
    for raw in path.read_text("utf-8").splitlines():
        if raw.startswith("issue: |"):
            section = "issue"
            continue
        if section == "issue":
            if raw.startswith("  ") or not raw.strip():
                issue.append(raw[2:])
                continue
            section = None
        if raw.startswith("expected:"):
            section = "expected"
            continue
        if raw.startswith("success:"):
            section = "success"
            continue
        if section == "expected" and raw.startswith("  "):
            k, _, v = raw.strip().partition(":")
            task["expected"][k.strip()] = v.strip()
            continue
        if section == "success" and raw.strip().startswith("- "):
            task["success"].append(raw.strip()[2:])
            continue
        if ":" in raw and not raw.startswith(" "):
            k, _, v = raw.partition(":")
            task[k.strip()] = v.strip()
            section = None
    task["issue"] = "\n".join(issue).strip()
    return task


def reset(repo: Path) -> None:
    for args in (["reset", "-q"], ["checkout", "--", "."],
                 ["clean", "-qfdx", "-e", ".fixture.json"]):
        subprocess.run(["git", *args], cwd=repo, capture_output=True)


def run_one(task: dict, tier: str | None, use_mock: bool) -> metrics.RunMetrics:
    repo = (ROOT / task["repository"].lstrip("./")).resolve()
    reset(repo)
    m = metrics.RunMetrics(fixture=task["id"], model_tier=tier or "T1",
                           task_type=task.get("task_type", ""),
                           reference_diff_lines=int(
                               task["expected"].get("reference_diff_lines", 0)
                               or 0))
    env_backup = dict(os.environ)
    try:
        if use_mock:
            import mock_model
            mock_model.install(_Patcher())
            os.environ["AI_API_KEY"] = "sk-ant-api03-BENCHMOCK1234567890"
        os.environ["REPO_PATH"] = str(repo)
        os.environ["ISSUE"] = task["issue"]
        os.environ["HARNESS_NO_CACHE"] = "1"
        for k in ("HARNESS_DRY_RUN", "HARNESS_TASK_TYPE", "HARNESS_ROUTE"):
            os.environ.pop(k, None)
        if tier:
            os.environ["HARNESS_TIER"] = tier
        else:
            os.environ.pop("HARNESS_TIER", None)

        started = time.time()
        cfg = C.load(["harness"])
        cfg.work_dir = Path(tempfile.mkdtemp()) / ".harness"
        buf = io.StringIO()
        orch = Orchestrator(cfg, Logger(stream=buf))
        m.exit_code = orch.run()
        m.wall_s = round(time.time() - started, 2)
        m.tokens_in = orch.budgets.tokens.used_in
        m.tokens_out = orch.budgets.tokens.used_out
        m.cache_read = orch.budgets.tokens.used_cached
        m.model_calls = sum(s["calls"] for s in
                            orch.budgets.tokens.per_phase.values())
        out = buf.getvalue()
        for line in out.splitlines():
            if line.startswith("[P1 INVESTIGATE] route "):
                m.route = line.split("route ")[1].split()[0]
            if "cycles used" in line:
                m.cycles = int(line.split("cycles used")[1].split("of")[0])
        m.oracle = m.route == "ORACLE"

        conf = cfg.work_dir / "run" / "confidence.json"
        if conf.is_file():
            d = json.loads(conf.read_text())
            m.confidence = f"{d.get('score', 0)}/6"
        ver = cfg.work_dir / "run" / "verification.json"
        if ver.is_file():
            d = json.loads(ver.read_text())
            cls = d.get("full") or d.get("scoped") or {}
            m.new_failures = len(cls.get("new", []))
            m.pre_existing = len(cls.get("pre_existing", []))
            m.fixed = len(cls.get("fixed", []))
            m.flaky = len(cls.get("flaky", []))

        m.diff_lines = evaluator.diff_lines(repo)
        resolved, reasons = evaluator.evaluate(repo, PYEXE, task,
                                               cfg.work_dir / "run")
        m.resolved = resolved
        m.notes = reasons
    finally:
        os.environ.clear()
        os.environ.update(env_backup)
        reset(repo)
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["T0", "T1", "T2"])
    ap.add_argument("--only")
    ap.add_argument("--out", default=str(ROOT / "bench" / "reports"))
    args = ap.parse_args(argv)

    use_mock = not os.environ.get("AI_API_KEY")
    tasks = sorted((ROOT / "bench" / "tasks").glob("*.yaml"))
    if args.only:
        tasks = [t for t in tasks if args.only in t.name]

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) / f"matrix-{stamp}.jsonl"
    print(f"{'fixture':18} {'route':7} {'res':4} {'cyc':4} {'diff':5} "
          f"{'conf':5} {'tokens':8} notes")
    print("-" * 88)
    rows = []
    for path in tasks:
        task = load_task(path)
        m = run_one(task, args.tier, use_mock)
        metrics.append(out, m)
        rows.append(m)
        print(f"{m.fixture:18} {m.route:7} "
              f"{'YES' if m.resolved else 'no':4} {m.cycles:<4} "
              f"{m.diff_lines:<5} {m.confidence:5} "
              f"{m.tokens_in + m.tokens_out:<8} "
              f"{'; '.join(m.notes)[:30]}")

    resolved = sum(1 for r in rows if r.resolved)
    print("-" * 88)
    print(f"resolved {resolved}/{len(rows)}   "
          f"tokens {sum(r.tokens_in + r.tokens_out for r in rows)}   "
          f"wall {sum(r.wall_s for r in rows):.0f}s")
    print(f"written: {out}")
    return 0 if resolved == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
