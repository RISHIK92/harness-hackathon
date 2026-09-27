"""Install the target repository's dependencies (SPEC.md 26.7).

A freshly cloned repository has no `node_modules` and no virtualenv, so its
suite cannot run: `jest: command not found`, exit 127. Verification is then
dead on arrival and C3 can never pass -- the harness is useless on exactly
the repositories it exists for.

Installing is not an ordinary local action, which is why it is gated like a
push rather than run freely:

  * it fetches code from a public registry, and
  * `npm install` executes arbitrary `postinstall` scripts from every
    transitive dependency.

So the default is `--ignore-scripts`, and the gate is asked first. Scripts can
be re-enabled deliberately (`HARNESS_INSTALL_SCRIPTS=1`) because some
packages genuinely need a build step -- prisma generate, native addons -- and
failing honestly is better than pretending those repositories are supported.

`npm install` and `pip install` stay on the runner's deny list throughout.
That list governs commands the *model* asks for; this module issues the
command itself, after the gate returns true, exactly as §37.2 does for push.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .runner import run

TIMEOUT = 600.0           # a cold install on a large repo is genuinely slow

NODE = "node"
PYTHON = "python"


@dataclass
class Install:
    needed: bool = False
    ran: bool = False
    ok: bool = False
    cmd: str = ""
    kind: str = ""
    reason: str = ""
    detail: str = ""

    def render(self) -> str:
        if not self.needed:
            return "dependencies already present"
        if not self.ran:
            return f"not installed: {self.reason}"
        return f"{self.cmd} -- {'ok' if self.ok else 'failed'}"


def _scripts_allowed() -> bool:
    raw = (os.environ.get("HARNESS_INSTALL_SCRIPTS") or "").strip().lower()
    return raw in ("1", "yes", "true", "on")


def _flag() -> str:
    return "" if _scripts_allowed() else " --ignore-scripts"


def _runner(tool: str) -> str:
    """`tool`, or corepack's copy of it when it is not installed.

    Repositories pin their package manager (`"packageManager": "pnpm@12"`)
    and often nobody has that manager on PATH. Node ships corepack exactly
    for this, and it fetches the pinned version -- so the alternative to
    using it is not "use npm instead", it is "the suite never runs".
    """
    import shutil
    if shutil.which(tool):
        return tool
    if tool != "npm" and shutil.which("corepack"):
        return f"corepack {tool}"
    return tool


def node_command(repo: Path) -> str:
    """Prefer the lockfile's own manager, and a reproducible install."""
    if (repo / "pnpm-lock.yaml").is_file():
        return f"{_runner('pnpm')} install --frozen-lockfile{_flag()}"
    if (repo / "yarn.lock").is_file():
        return f"{_runner('yarn')} install --frozen-lockfile{_flag()}"
    if (repo / "package-lock.json").is_file():
        # `npm ci` is the reproducible one, but it refuses when the lockfile
        # is out of step with package.json, so fall back rather than fail.
        return f"{_runner('npm')} ci{_flag()}"
    return f"{_runner('npm')} install{_flag()}"


def python_command(repo: Path, interpreter: str) -> str:
    if (repo / "requirements.txt").is_file():
        return f"{interpreter} -m pip install -q -r requirements.txt"
    if (repo / "pyproject.toml").is_file() or (repo / "setup.py").is_file():
        return f"{interpreter} -m pip install -q -e ."
    return ""


def _node_needed(repo: Path) -> bool:
    if not (repo / "package.json").is_file():
        return False
    modules = repo / "node_modules"
    return not modules.is_dir() or not any(modules.iterdir())


def _python_needed(repo: Path, toolchain) -> bool:
    """Only when the interpreter that will run the tests cannot import the
    framework. A repository whose dependencies are already importable must
    not be touched."""
    manifest = any((repo / f).is_file() for f in
                   ("requirements.txt", "pyproject.toml", "setup.py"))
    if not manifest:
        return False
    framework = (getattr(toolchain, "framework", "") or "pytest")
    from .toolchain import python_interpreter
    interp = python_interpreter(repo, needs=framework)
    if not interp:
        return True
    return run(f"{interp} -c \"import {framework}\"", repo, timeout=30,
               check_deny=False).exit_code != 0


def assess(repo: Path, toolchain) -> Install:
    """What is missing, and what would fix it. Runs nothing."""
    lang = (getattr(toolchain, "language", "") or "").lower()
    if lang in ("javascript", "typescript") and _node_needed(repo):
        return Install(needed=True, kind=NODE, cmd=node_command(repo))
    if lang == "python" and _python_needed(repo, toolchain):
        from .toolchain import python_interpreter
        interp = python_interpreter(repo, needs="pip") or "python3"
        cmd = python_command(repo, interp)
        if cmd:
            return Install(needed=True, kind=PYTHON, cmd=cmd)
    return Install(needed=False)


# What a package manager writes as a side effect of installing. None of it
# is the harness's change, and letting it into the diff fails C4 -- the run
# is reported PARTIAL for a lockfile the operator never asked to touch.
ARTIFACTS = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
             "pnpm-lock.yaml", "poetry.lock")


def _snapshot(repo: Path) -> dict:
    out = {}
    for name in ARTIFACTS:
        path = repo / name
        try:
            out[name] = path.read_bytes() if path.is_file() else None
        except OSError:
            out[name] = None
    return out


def _restore(repo: Path, before: dict, log) -> list:
    """Put the lockfiles back exactly as they were.

    Installing is setup, not the fix. A lockfile the installer created or
    rewrote is an artefact of making the suite runnable, and the diff the
    evaluator reads must contain the change and nothing else.
    """
    touched = []
    for name, original in before.items():
        path = repo / name
        try:
            current = path.read_bytes() if path.is_file() else None
            if current == original:
                continue
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(original)
            touched.append(name)
        except OSError:
            continue
    if touched:
        log.note("install_artifacts",
                 "restored " + ", ".join(touched) + " after installing")
    return touched


def ensure(repo: Path, toolchain, gate, log) -> Install:
    """Install if needed and permitted. Never raises."""
    try:
        plan = assess(repo, toolchain)
    except OSError as exc:
        return Install(needed=False, detail=str(exc))
    if not plan.needed:
        return plan

    from .. import consent
    rows = [("repository", repo.name),
            ("command", plan.cmd),
            ("scripts", "allowed" if _scripts_allowed()
                        else "disabled (--ignore-scripts)")]
    # `requested`, for the same reason cloning is (§37.2): "fix this issue
    # in this repository" is an instruction to make that repository
    # runnable, and a suite that cannot start makes the whole run pointless.
    # An unattended evaluator run has nobody to ask, and refusing there
    # would refuse the command it was just given. The safety is in
    # --ignore-scripts being the default, and HARNESS_AUTO=never still
    # blocks it outright.
    if not gate.allow(consent.INSTALL,
                      f"install dependencies with {plan.cmd.split()[0]}",
                      rows, requested=True):
        plan.reason = "declined"
        log.degraded("no_deps", "dependencies not installed; the suite "
                                "may not run")
        return plan

    log.working(f"installing dependencies ({plan.cmd.split()[0]})")
    before = _snapshot(repo)
    result = run(plan.cmd, repo, timeout=TIMEOUT, check_deny=False)
    plan.ran = True
    plan.ok = result.exit_code == 0

    if not plan.ok and plan.kind == NODE and plan.cmd.startswith("npm ci"):
        # `npm ci` fails outright when the lockfile drifted from the
        # manifest. That is a repository problem, not a reason to give up.
        plan.cmd = f"{_runner('npm')} install{_flag()}"
        result = run(plan.cmd, repo, timeout=TIMEOUT, check_deny=False)
        plan.ok = result.exit_code == 0

    _restore(repo, before, log)
    log.done_working()
    if plan.ok:
        log.ok("dependencies", plan.cmd, plain=f"dependencies: {plan.cmd}")
    else:
        tail = ((result.stdout or "") + (result.stderr or ""))[-300:].strip()
        plan.detail = tail
        log.degraded("deps_failed", f"{plan.cmd} exited {result.exit_code}")
        if not _scripts_allowed() and "postinstall" in tail.lower():
            log.note("install_scripts",
                     "a package needs its install scripts: retry with "
                     "HARNESS_INSTALL_SCRIPTS=1")
    return plan
