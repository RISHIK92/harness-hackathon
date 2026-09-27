"""Getting to a runnable environment (SPEC.md 39.2).

The harness cannot verify a fix it cannot run, so before anything else it has
to answer: can this machine build and test this repository, and if not, what
is the cheapest honest way to make it so?

That question has several answers and they are not interchangeable, so this
is a ladder rather than a single strategy. Each rung states what it costs,
what it needs, and what it would change:

    host           nothing is missing                    free
    corepack       a pinned pnpm/yarn, fetched on use    seconds, no install
    venv           a project virtualenv beside the code  seconds, local
    container      the runtime the repo declares         a pull, isolated
    install-docker ask first; it changes the machine     minutes, gated

The order is deliberate: try what costs nothing, then what is local and
reversible, then what is isolated, and only then propose changing the
operator's machine -- which is always asked for and never assumed. A refusal
is not a failure: the ladder continues, and running on an imperfect host with
that fact recorded beats stopping.

Every rung reports why it was chosen or skipped, because "the suite did not
run" is the one failure that silently invalidates everything downstream.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

INSTALL_TIMEOUT = 900.0


@dataclass
class Decision:
    """What was chosen, what was not, and why."""
    strategy: str = "host"
    detail: str = ""
    gap: str = ""                      # what the host could not provide
    considered: list = field(default_factory=list)
    container: object = None

    def render(self) -> str:
        if not self.gap:
            return "host (nothing missing)"
        return f"{self.strategy}: {self.detail}" if self.detail else self.strategy

    def to_json(self) -> dict:
        return {"strategy": self.strategy, "detail": self.detail,
                "gap": self.gap, "considered": self.considered}


def use_environment() -> bool:
    """HARNESS_LIVE_ENV: may the harness prepare this machine?

    Off by default. Cloning and editing needs nothing installed, and most
    runs are read-mostly: a dependency install costs minutes on a real
    repository and changes the working copy before the first edit is even
    proposed. Preparing an environment is a choice, so it is made
    explicitly.

    The cost of leaving it off is stated rather than hidden: without
    dependencies the suite usually cannot run, so C3 has nothing to observe
    and the run reports weaker verification instead of a green tick it did
    not earn.
    """
    raw = (os.environ.get("HARNESS_LIVE_ENV") or "").strip().lower()
    return raw in ("1", "on", "yes", "true", "always")


def _docker_installer() -> tuple[str, str] | None:
    """A command that installs Docker here, if there is an obvious one."""
    system = platform.system()
    if system == "Darwin" and shutil.which("brew"):
        return "brew install --cask docker", "Homebrew"
    if system == "Linux":
        if shutil.which("apt-get"):
            return "apt-get install -y docker.io", "apt"
        if shutil.which("dnf"):
            return "dnf install -y docker", "dnf"
    if system == "Windows" and shutil.which("winget"):
        return "winget install -e --id Docker.DockerDesktop", "winget"
    return None


def _python_venv_gap(repo: Path, toolchain) -> str:
    """Can the host's interpreter import what the suite needs?"""
    from .verify.toolchain import _host_python_interpreter

    manifest = any((repo / f).is_file() for f in
                   ("pyproject.toml", "requirements.txt", "setup.py"))
    if not manifest:
        return ""
    framework = (getattr(toolchain, "framework", "") or "pytest")
    interp = _host_python_interpreter(repo, needs=framework)
    if not interp:
        return "no python interpreter on this machine"
    probe = subprocess.run([interp, "-c", f"import {framework}"],
                           capture_output=True, timeout=30,
                           stdin=subprocess.DEVNULL)
    return "" if probe.returncode == 0 else f"{framework} is not installed"


def assess(repo: Path, toolchain) -> str:
    """What the host cannot provide. Empty means it is ready."""
    from . import container

    gap = container.host_satisfies(repo, toolchain)
    if gap:
        return gap
    return _python_venv_gap(repo, toolchain)


# -- the rungs --------------------------------------------------------------

def _try_venv(repo: Path, gap: str, ctx) -> Decision | None:
    """A virtualenv beside the code. Local, reversible, needs no permission."""
    if "not installed" not in gap and "interpreter" not in gap:
        return None
    from .verify.runner import run

    if not any((repo / f).is_file() for f in
               ("pyproject.toml", "requirements.txt", "setup.py")):
        return None
    base = shutil.which("python3") or shutil.which("python")
    if not base:
        return None

    ctx.log.working("creating a virtualenv for the project")
    made = run(f"{base} -m venv .venv", repo, timeout=180, check_deny=False)
    if made.exit_code != 0:
        ctx.log.done_working()
        return None
    py = ".venv/bin/python" if (repo / ".venv/bin/python").is_file() \
        else ".venv/Scripts/python.exe"
    installs = []
    if (repo / "requirements.txt").is_file():
        installs.append(f"{py} -m pip install -q -r requirements.txt")
    elif (repo / "pyproject.toml").is_file() or (repo / "setup.py").is_file():
        installs.append(f"{py} -m pip install -q -e .")
    installs.append(f"{py} -m pip install -q pytest")
    for cmd in installs:
        run(cmd, repo, timeout=600, check_deny=False)
    ctx.log.done_working()

    framework = (getattr(ctx.toolchain, "framework", "") or "pytest")
    ok = run(f"{py} -c \"import {framework}\"", repo, timeout=60,
             check_deny=False).exit_code == 0
    if not ok:
        return None
    ctx.log.ok("environment", f"project virtualenv ({py})",
               plain=f"environment: project virtualenv")
    return Decision(strategy="venv", detail=str(py), gap=gap)


def _try_container(repo: Path, gap: str, ctx) -> Decision | None:
    from . import consent, container
    from .verify import runner

    if not container.docker_available():
        return None
    env = container.detect(repo, ctx.toolchain)
    if not env.available:
        return None
    if ctx.gate is not None and not ctx.gate.allow(
            consent.INSTALL, f"run this repository in {env.image}",
            [("image", env.image), ("chosen from", env.why),
             ("because", gap)], requested=True):
        return None
    box = container.Container(repo=repo, image=env.image)
    if not box.start(ctx.log):
        return None
    runner.use_container(box)
    return Decision(strategy="container", detail=env.image, gap=gap,
                    container=box)


def _try_install_docker(repo: Path, gap: str, ctx) -> Decision | None:
    """Changing the operator's machine. Always asked for, never assumed."""
    from . import consent

    if container_present():
        return None
    installer = _docker_installer()
    if not installer:
        return None
    cmd, via = installer
    if ctx.gate is None:
        ctx.log.note("skipped", "installing Docker needs permission; set "
                                "HARNESS_AUTO=install to allow it unattended")
        return None
    if not ctx.gate.allow(
            consent.INSTALL, f"install Docker via {via}",
            [("command", cmd), ("because", gap),
             ("note", "this changes this machine, not just the repository")]):
        return None

    from .verify.runner import run
    ctx.log.working(f"installing Docker via {via}")
    result = run(cmd, repo, timeout=INSTALL_TIMEOUT, check_deny=False)
    ctx.log.done_working()
    if result.exit_code != 0:
        ctx.log.degraded("docker_install_failed",
                         (result.stderr or result.stdout)[-160:].strip())
        return None
    return _try_container(repo, gap, ctx)


def container_present() -> bool:
    from . import container
    return container.docker_available()


# Rungs are named, not bound. Holding the function objects here froze the
# ladder at import time: it could not be substituted, extended, or tested
# without reaching inside it.
LADDER = (
    ("venv", "_try_venv"),
    ("container", "_try_container"),
    ("install-docker", "_try_install_docker"),
)


def provision(repo: Path, ctx) -> Decision:
    """Walk the ladder until the repository can be built and tested.

    Never raises: an environment that could not be improved is reported and
    the run continues on the host, because a verdict from an imperfect
    environment with that fact recorded is worth more than no verdict.
    """
    from . import container

    if not use_environment():
        return Decision(detail="environment setup is off "
                               "(HARNESS_LIVE_ENV=1 to enable)",
                        considered=["host"])

    want = container.mode()
    gap = assess(repo, ctx.toolchain)

    if want == "off":
        return Decision(gap=gap, detail="containers disabled",
                        considered=["host"])
    if not gap and want != "always":
        return Decision(considered=["host"])

    ctx.log.line(f"environment: {gap or 'forcing an isolated runtime'}")
    considered = []
    for name, attr in LADDER:
        considered.append(name)
        rung = globals().get(attr)
        if rung is None:
            continue
        try:
            chosen = rung(repo, gap or "HARNESS_DOCKER=always", ctx)
        except Exception as exc:          # a rung must never end the run
            ctx.log.debug(f"{name} failed: {exc}")
            chosen = None
        if chosen is not None:
            chosen.considered = considered
            return chosen

    ctx.log.degraded("unprovisioned",
                     f"{gap}; continuing on the host, so verification may "
                     f"be incomplete")
    return Decision(gap=gap, detail="no option available",
                    considered=considered)
