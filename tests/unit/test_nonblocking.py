"""No automated path may ever block waiting for a human.

An evaluator running `make setup && make run` in a script, or piping, or in
CI, must never meet a prompt. A hang here makes the submission unscorable,
which is the worst failure available to us -- worse than a wrong answer,
because it produces no answer at all.

These run the real entry points in a subprocess with a hard timeout.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PYEXE = str((ROOT / ".venv" / "bin" / "python").resolve())
TIMEOUT = 60

pytestmark = pytest.mark.skipif(
    not Path(PYEXE).exists(), reason="run make setup first")


def _env(**extra) -> dict:
    env = {**os.environ,
           "AI_API_KEY": "sk-ant-api03-FAKEBLOCKTEST1234567890",
           "REPO_PATH": str(ROOT / "tests" / "fixtures" / "py-offbyone")}
    env.pop("ISSUE", None)
    env.pop("ISSUE_FILE", None)
    env.update(extra)
    return env


def _run(cmd: str, stdin=subprocess.DEVNULL, **env) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, cwd=ROOT, env=_env(**env), stdin=stdin,
                              shell=True, capture_output=True, text=True,
                              timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        pytest.fail(f"BLOCKED: `{cmd}` waited for input in an automated run")


def test_run_with_no_issue_and_closed_stdin_exits():
    r = _run(f"{PYEXE} -m harness")
    assert r.returncode != 0
    assert "No issue supplied" in (r.stdout + r.stderr)


def test_run_through_make_with_closed_stdin_exits():
    r = _run("make run")
    assert r.returncode != 0


def test_issue_piped_on_stdin_is_read():
    r = _run(f'echo "parse_date crashes with IndexError" | {PYEXE} -m harness',
             stdin=None)
    # it gets as far as the provider, which is what matters: it did not wait
    assert "No issue supplied" not in (r.stdout + r.stderr)


def test_noninteractive_env_forces_the_scripted_path():
    r = _run(f"{PYEXE} -m harness", HARNESS_NONINTERACTIVE="1")
    assert r.returncode != 0
    assert "No issue supplied" in (r.stdout + r.stderr)


def test_replay_needs_no_key_and_terminates():
    env = _env()
    env.pop("AI_API_KEY", None)
    try:
        r = subprocess.run(f"{PYEXE} -m harness.replay", cwd=ROOT, env=env,
                           stdin=subprocess.DEVNULL, shell=True,
                           capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        pytest.fail("BLOCKED: make test waited for input")
    assert r.returncode in (0, 1, 2)


def test_setup_terminates_and_is_never_interactive():
    r = _run(f"{PYEXE} -m harness.setup_ui")
    assert r.returncode == 0
    out = r.stdout
    assert "ready" in out
    assert "tab" not in out.lower(), "setup must not offer a keystroke menu"


def test_the_console_module_is_never_entered_without_a_tty():
    """Belt and braces: assert the guard itself, in-process."""
    sys.path.insert(0, str(ROOT))
    from harness import config as C
    from harness import console

    cfg = C.Config(api_key="k", issue="", repo_path=ROOT)
    assert console.interactive(cfg) is False
