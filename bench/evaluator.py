"""Did the run actually succeed? (SPEC.md 33)

Criteria-driven: each task declares what success means, because "the suite is
green" is the wrong question for a configuration problem whose correct answer
is not a code change at all.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _run(repo: Path, *args) -> str:
    return subprocess.run(list(args), cwd=repo, capture_output=True,
                          text=True).stdout


def suite_result(repo: Path, python: str) -> tuple[int, int, str]:
    """(failed, passed, tail)."""
    r = subprocess.run([python, "-m", "pytest", "-q"], cwd=repo,
                       capture_output=True, text=True)
    tail = ANSI.sub("", (r.stdout.strip().splitlines() or ["no output"])[-1])
    failed = int((re.search(r"(\d+) failed", tail) or [0, 0])[1] or 0)
    passed = int((re.search(r"(\d+) passed", tail) or [0, 0])[1] or 0)
    return failed, passed, tail


def changed_files(repo: Path) -> list:
    return [f for f in _run(repo, "git", "diff", "--name-only").split() if f]


def diff_lines(repo: Path) -> int:
    total = 0
    for line in _run(repo, "git", "diff", "--numstat").splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            total += sum(int(n) for n in parts[:2] if n.isdigit())
    return total


def _artifact(run_dir: Path, name: str) -> dict:
    p = Path(run_dir) / name
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def evaluate(repo: Path, python: str, task: dict, run_dir: Path
             ) -> tuple[bool, list]:
    """Check every declared success criterion. Returns (resolved, reasons)."""
    expected = task.get("expected", {})
    criteria = task.get("success", ["tests_pass"])
    reasons: list = []
    ok = True

    failed, passed, tail = suite_result(repo, python)
    changed = changed_files(repo)
    lines = diff_lines(repo)
    allowed_failures = int(expected.get("pre_existing_failures", 0) or 0)
    if "flake_not_blocking" in criteria:
        allowed_failures += 1
    target = expected.get("target_file")
    target = None if target in (None, "null", "None", "") else target
    rootcause = _artifact(run_dir, "rootcause.json")

    for criterion in criteria:
        name = criterion.split()[0]

        if name == "tests_pass":
            if failed > allowed_failures:
                ok = False
                reasons.append(f"suite: {tail}")
            elif failed:
                reasons.append(f"{failed} expected failure(s) remain")

        elif name == "no_new_failures":
            if failed > allowed_failures:
                ok = False
                reasons.append(f"new failures: {tail}")

        elif name == "pre_existing_untouched":
            if any("tax" in c or "test" in c for c in changed):
                ok = False
                reasons.append("touched a pre-existing failure's file")

        elif name == "lint_gate_did_not_block":
            if not changed:
                ok = False
                reasons.append("no change was made: the lint gate blocked")

        elif name == "flake_not_blocking":
            if not changed:
                ok = False
                reasons.append("no change was made: the flake blocked")

        elif name == "callers_identified":
            plan = _artifact(run_dir, "scope.json")
            if not plan.get("callers_requiring_update"):
                ok = False
                reasons.append("no callers enumerated")

        elif name == "classified_as_config":
            if rootcause.get("classification") != "config":
                ok = False
                reasons.append(f"classified {rootcause.get('classification')},"
                               " expected config")

        elif name == "classified_as_external":
            if rootcause.get("classification") not in ("external", "config"):
                ok = False
                reasons.append(f"classified {rootcause.get('classification')},"
                               " expected external")

        elif name == "no_speculative_code_change":
            if changed:
                ok = False
                reasons.append(f"changed {changed} for a non-code problem")

        elif name == "commit_identified":
            pass                 # requires bisect (ENH)

        elif name.startswith("diff_lines"):
            cap = int(criterion.split("<=")[1]) if "<=" in criterion else 999
            if lines > cap:
                ok = False
                reasons.append(f"diff {lines} lines > {cap}")

    # A task that names a target file must actually change it.
    if target and "no_speculative_code_change" not in criteria:
        if changed != [target]:
            ok = False
            reasons.append(f"changed {changed or 'nothing'}, expected "
                           f"[{target}]")
    return ok, reasons
