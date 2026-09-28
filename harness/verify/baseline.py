"""Pre-edit snapshot (SPEC.md 9.2, 26.1).

FR-29 and FR-30 are impossible without this: you cannot tell a regression you
caused from a test that was already red.  The run is instrumented with
`coverage` so SBFL (SPEC.md 21.1) comes free.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import parse_results as P
from .runner import run

BASELINE_CAP_S = 300.0

COVERAGE_RC = """[run]
dynamic_context = test_function
branch = false
"""


@dataclass
class Baseline:
    tests: dict = field(default_factory=dict)
    failures: dict = field(default_factory=dict)
    lint: set = field(default_factory=set)
    mode: str = "absent"                 # full | scoped | absent
    duration_s: float = 0.0
    confidence: str = "high"
    coverage: dict = field(default_factory=dict)   # file -> {line: [contexts]}
    coverage_ok: bool = False
    notes: list = field(default_factory=list)

    @property
    def red(self) -> bool:
        return any(s in P.FAILING for s in self.tests.values())

    def to_json(self) -> dict:
        return {"tests": self.tests, "mode": self.mode,
                "duration_s": round(self.duration_s, 2),
                "confidence": self.confidence,
                "lint": sorted(self.lint), "coverage_ok": self.coverage_ok,
                "notes": self.notes,
                "counts": {k: v for k, v in
                           P.TestResults(tests=self.tests).counts().items()}}


def _coverage_available(repo: Path, python: str) -> bool:
    # Harness constant on the discovered interpreter; no model text.
    r = run(f"{python} -m coverage --version", repo, timeout=20,
            check_deny=False)
    return r.ok


def capture(repo: Path, toolchain, cfg, log, work_dir: Path) -> Baseline:
    """Run the suite (instrumented when possible) before any edit."""
    bl = Baseline()
    if not toolchain or not toolchain.test_cmd:
        bl.notes.append("no test command discovered")
        log.degraded("no_tests", "verification is lint + syntax + judges only")
        return bl

    work_dir.mkdir(parents=True, exist_ok=True)
    junit = work_dir / "baseline-junit.xml"
    cov_json = work_dir / "coverage.json"

    python = getattr(toolchain, "python", "python3")
    use_cov = (not cfg.no_coverage and toolchain.language == "python"
               and _coverage_available(repo, python))
    rc_path = None
    cmd = toolchain.test_cmd
    if toolchain.junit_flag:
        cmd = f"{cmd} {toolchain.junit_flag.format(path=junit)}"
    if use_cov:
        rc_path = work_dir / "coverage.rc"
        rc_path.write_text(COVERAGE_RC, encoding="utf-8")
        cmd = _instrument(cmd, rc_path, python)

    log.debug(f"baseline command: {cmd}")
    # The repository's own suite (CI, Makefile, manifest or the operator's
    # HARNESS_TEST_CMD) with harness-built flags. No model text: the deny
    # list would only refuse a project whose test script it dislikes.
    result = run(cmd, repo, timeout=BASELINE_CAP_S, check_deny=False,
                 env_extra={"COVERAGE_FILE": str(work_dir / ".coverage")})
    bl.duration_s = result.duration_s

    if result.timed_out:
        bl.mode = "scoped"
        bl.notes.append(f"suite exceeded the {BASELINE_CAP_S:.0f}s cap")
        log.degraded("slow_suite", "baseline is scoped, not full")

    parsed = P.parse(result, toolchain, junit_path=junit)
    bl.tests = parsed.tests
    bl.failures = parsed.failures
    bl.confidence = parsed.confidence
    if parsed.confidence == "low":
        log.degraded("weak_parse", "test ids may be unstable; judges promoted")
    if not bl.tests:
        bl.mode = bl.mode if bl.mode != "absent" else "absent"
        bl.notes.append("no tests parsed")
    elif bl.mode == "absent":
        bl.mode = "full"

    if use_cov:
        bl.coverage, bl.coverage_ok = _read_coverage(repo, rc_path, cov_json,
                                                     work_dir, log, python)
    elif not cfg.no_coverage and toolchain.language == "python":
        log.degraded("no_coverage", "SBFL localization unavailable")

    from .lint_gate import capture_baseline as lint_baseline
    bl.lint = lint_baseline(repo, toolchain, log)

    counts = parsed.counts()
    body = (f"{counts[P.PASS]} passed, "
            f"{counts[P.FAIL] + counts[P.ERROR]} failed, "
            f"{counts[P.SKIP]} skipped  "
            f"({bl.duration_s:.1f}s, mode={bl.mode})")
    log.computed("baseline", body, plain=f"baseline: {body}")
    if bl.red:
        log.note("red_baseline",
                 f"{len(parsed.failing)} tests already failing - these will be "
                 "documented, never fixed")
    return bl


def _instrument(cmd: str, rc: Path, python: str) -> str:
    """Wrap a python test command with `coverage run`."""
    rcflag = f"--rcfile={rc}"
    marker = " -m "
    if marker in cmd:
        head, rest = cmd.split(marker, 1)
        return f"{head}{marker}coverage run {rcflag} -m {rest}"
    return f"{python} -m coverage run {rcflag} -m {cmd}"


def _read_coverage(repo: Path, rc: Path, out: Path, work_dir: Path,
                   log, python: str = "python3") -> tuple[dict, bool]:
    # Harness-built coverage query on harness paths; no model text.
    r = run(f"{python} -m coverage json --rcfile={rc} --show-contexts -o {out}",
            repo, timeout=60, check_deny=False,
            env_extra={"COVERAGE_FILE": str(work_dir / ".coverage")})
    if not r.ok or not out.is_file():
        log.degraded("no_coverage", "coverage json failed")
        return {}, False
    try:
        data = json.loads(out.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        log.degraded("no_coverage", "coverage json unreadable")
        return {}, False

    files = {}
    for path, info in (data.get("files") or {}).items():
        ctxs = info.get("contexts") or {}
        keep = {int(line): [c for c in names if c]
                for line, names in ctxs.items() if any(names)}
        if keep:
            files[path] = keep
    return files, bool(files)
