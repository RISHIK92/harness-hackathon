"""Sandboxed command execution (SPEC.md 5.4).

Stateless invocations: no persistent shell, so `cd` is emulated by an
orchestrator-held cwd.  That kills a class of state bugs and makes swapping
in `docker exec` a one-line change.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass

HEAD_CAP = 200_000
TAIL_CAP = 50_000

# Hard refusals. Checked against the rendered command string.
DENY = [
    (re.compile(r"\brm\s+-rf\s+/(?:\s|$)"), "rm -rf /"),
    (re.compile(r"\bsudo\b"), "sudo"),
    (re.compile(r"\bgit\s+push\b"), "git push"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "git reset --hard"),
    (re.compile(r"\bgit\s+clean\b"), "git clean"),
    (re.compile(r"\bcurl\b[^|]*\|\s*(ba)?sh"), "curl | sh"),
    (re.compile(r"\bwget\b[^|]*\|\s*(ba)?sh"), "wget | sh"),
    (re.compile(r"\b(apt-get|apt|yum|dnf|brew|pacman)\s+install\b"),
     "system package install"),
    (re.compile(r"\bpip\s+install\b"), "pip install into the target repo"),
    (re.compile(r"\bnpm\s+(install|i)\b"), "npm install"),
    (re.compile(r"\b(shutdown|reboot|halt|mkfs|dd\s+if=)"), "destructive"),
    (re.compile(r"\bnohup\b|\bdisown\b|&\s*$"), "background process"),
    (re.compile(r">\s*/dev/(sd|nvme|disk)"), "raw device write"),
]


class CommandRefused(Exception):
    pass


@dataclass
class Result:
    cmd: str
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def output(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()


def check_allowed(cmd: str) -> None:
    for pattern, label in DENY:
        if pattern.search(cmd):
            raise CommandRefused(f"refused: {label}  ({cmd[:80]})")


# The active container, or None for the host. Module level on purpose: it is
# a property of the run, not of any one call.
_CONTAINER = None


def use_container(box) -> None:
    """Route repository commands into `box`. None restores the host."""
    global _CONTAINER
    _CONTAINER = box


def active_container():
    return _CONTAINER


def _clean_env(repo_path) -> dict:
    """A scrubbed copy of the environment. The API key never crosses this line."""
    env = {k: v for k, v in os.environ.items()
           if not any(tok in k.upper()
                      for tok in ("API_KEY", "TOKEN", "SECRET", "PASSWORD",
                                  "CREDENTIAL", "HARNESS_"))}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["NO_COLOR"] = "1"
    env["CI"] = "1"
    env.pop("VIRTUAL_ENV", None)
    return env


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= HEAD_CAP + TAIL_CAP:
        return text, False
    head, tail = text[:HEAD_CAP], text[-TAIL_CAP:]
    elided = len(text) - HEAD_CAP - TAIL_CAP
    return f"{head}\n... {elided} characters elided ...\n{tail}", True


def run(cmd: str, cwd, timeout: float = 120.0, env_extra: dict | None = None,
        check_deny: bool = True, truncate: bool = True) -> Result:
    """Run one command to completion. Never raises except on a denied command.

    stdin is /dev/null, always. A command that waits on input would otherwise
    hang until the timeout while reading the *harness's* terminal -- stealing
    the operator's keystrokes on the way. `npx node` on a repo with no test
    framework is exactly that: it opens a REPL and sits there. With no stdin
    it exits immediately instead, and the failure is visible in a second.
    """
    if not cmd or not str(cmd).strip():
        # No test command discovered is a normal state for a repo without a
        # suite. It must read as a failed command, not raise from inside
        # subprocess and take the run down with it.
        return Result(cmd="", exit_code=127, stdout="",
                      stderr="no command to run", duration_s=0.0)
    if check_deny:
        check_allowed(cmd)

    env = _clean_env(cwd)
    if env_extra:
        env.update(env_extra)

    # A container, when one is active. Set once by the orchestrator so the
    # thirty-odd call sites keep their signatures and stay unaware of where
    # the command actually lands.
    box = _CONTAINER
    argv = box.exec_argv(cmd, cwd, env) if box is not None else None

    started = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            argv if argv is not None else cmd,
            cwd=None if argv is not None else str(cwd),
            shell=argv is None, env=None if argv is not None else env,
            timeout=timeout, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, errors="replace")
        code, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        code = 124
        out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(
            exc.stdout, bytes) else (exc.stdout or "")
        err = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(
            exc.stderr, bytes) else (exc.stderr or "")
        err = (err or "") + f"\n[timed out after {timeout:.0f}s]"
    except OSError as exc:
        code, out, err = 127, "", f"could not execute: {exc}"

    # Truncation protects the model's context, and is wrong for output the
    # harness parses itself. `git ls-files` on a 30k-file repository is over
    # a megabyte: capped, the file index silently held the alphabetically
    # first 4.5k paths and the harness was blind to the rest of the tree.
    if truncate:
        out, t1 = _truncate(out)
        err, t2 = _truncate(err)
    else:
        t1 = t2 = False
    return Result(cmd=cmd, exit_code=code, stdout=out, stderr=err,
                  duration_s=time.time() - started, timed_out=timed_out,
                  truncated=t1 or t2)


def which(binary: str) -> bool:
    import shutil
    return shutil.which(binary) is not None
