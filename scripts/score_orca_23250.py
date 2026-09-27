#!/usr/bin/env python3
"""Did the harness solve stablyai/orca#23250?

The issue: on POSIX the startup command is submitted with LF. The Enter key
sends CR, and an interactive line editor in raw mode routes ^J through the
user's bindings -- so a rebound ^J means the command is typed and never runs.
The fix the issue asks for is CR on every platform.

Scored against the code, not against the run's own verdict.

    python3 scripts/score_orca_23250.py <repo>
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Where the platform-conditional submit byte lives.
SOURCE_SITES = (
    "src/relay/pty-handler.ts",
    "src/main/providers/local-pty-shell-ready-startup-command.ts",
    "src/main/daemon/terminal-host-session-create.ts",
)

# Tests that assert the OLD behaviour. A complete fix updates them; the
# harness is not allowed to (they are on its never-touch list), so a run
# that leaves these red is behaving correctly, not failing.
DEPENDENT_TESTS = (
    "src/relay/pty-handler-startup-command-delivery.test.ts",
    "src/main/daemon/terminal-host-startup.test.ts",
)

OLD = re.compile(r"process\.platform\s*===\s*['\"]win32['\"]\s*\?\s*"
                 r"['\"]\\r['\"]\s*:\s*['\"]\\n['\"]")
NEW_CR = re.compile(r"const\s+submit\s*=\s*['\"]\\r['\"]")


def read(repo: Path, rel: str) -> str:
    try:
        return (repo / rel).read_text("utf-8", errors="replace")
    except OSError:
        return ""


def main(repo_arg: str) -> int:
    repo = Path(repo_arg).resolve()
    if not (repo / "package.json").is_file():
        print(f"not a checkout: {repo}")
        return 2

    print(f"scoring {repo}\n")
    fixed, stale = [], []
    for rel in SOURCE_SITES:
        body = read(repo, rel)
        if not body:
            stale.append((rel, "missing"))
            continue
        if OLD.search(body):
            stale.append((rel, "still LF on POSIX"))
        elif NEW_CR.search(body):
            fixed.append(rel)
        else:
            stale.append((rel, "no recognisable submit byte"))

    print("1. the fix itself")
    for rel in SOURCE_SITES:
        mark = "OK  " if rel in fixed else "MISS"
        why = next((w for r, w in stale if r == rel), "submits CR")
        print(f"   [{mark}] {rel}\n          {why}")

    print("\n2. does anything still submit LF?")
    leaked = []
    for path in repo.rglob("src/**/*.ts"):
        rel = str(path.relative_to(repo))
        if ".test." in rel:
            continue
        if OLD.search(read(repo, rel)):
            leaked.append(rel)
    print(f"   [{'OK  ' if not leaked else 'MISS'}] "
          f"{len(leaked)} non-test file(s) still platform-conditional")
    for rel in leaked[:5]:
        print(f"          {rel}")

    print("\n3. the suite")
    proc = subprocess.run(["npm", "test", "--silent"], cwd=repo,
                          capture_output=True, text=True, timeout=1800,
                          stdin=subprocess.DEVNULL)
    out = proc.stdout + proc.stderr
    failing = sorted({m.group(1).strip() for m in
                      re.finditer(r"^\s*[✖✗]\s+(.+?)\s*\([\d.]+m?s\)",
                                  out, re.M)})
    print(f"   exit {proc.returncode}, {len(failing)} failing test(s)")

    expected_red = [t for t in failing
                    if any(Path(d).stem.split('.')[0] in t.lower().replace(' ', '-')
                           for d in DEPENDENT_TESTS)]
    print(f"   of those, {len(expected_red)} are the tests that encode the "
          f"old behaviour")

    print("\n" + "=" * 62)
    all_fixed = len(fixed) == len(SOURCE_SITES) and not leaked
    if all_fixed and proc.returncode == 0:
        print("SOLVED - fix applied and the suite is green")
        return 0
    if all_fixed:
        print("FIX CORRECT, SUITE RED - the dependent tests still assert LF.")
        print("The harness may not edit tests, so this is the best outcome")
        print("available to it; a human completes it by updating them.")
        return 1
    print(f"NOT SOLVED - {len(SOURCE_SITES) - len(fixed)} site(s) unchanged")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
