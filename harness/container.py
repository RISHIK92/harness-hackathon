"""Run the repository's commands in a container (SPEC.md 39).

The host is the wrong place to build somebody else's project. A real
repository pins a toolchain the host does not have -- orca pins pnpm 12 and
a Node major; a Python service pins 3.11 while the host runs 3.13 -- and the
harness either cannot run the suite at all or runs it against the wrong
runtime and believes the answer. Installing 137 dependencies onto the
operator's machine to find out is also not a neutral act.

So: when Docker is available and the repository says what it needs, the
repository's commands run inside a container. Three properties follow, and
they are the reason this exists rather than more toolchain heuristics:

  * the runtime is the one the project declares, not the one the host has;
  * `npm install` writes into the container, not into someone's home;
  * a run is reproducible on a different machine.

Deliberately narrow:

  * Only the TARGET repository's commands are containerised. git and gh stay
    on the host, where the credentials are and where they belong.
  * Images are pulled, never built. Building a repository's Dockerfile is
    slow, frequently needs secrets, and fails for reasons that have nothing
    to do with the issue being fixed.
  * Absent Docker, or an image that will not start, is a degradation and not
    an error: the run continues on the host exactly as before.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

WORKDIR = "/work"
START_TIMEOUT = 300.0          # pulling a base image on a cold host
STOP_TIMEOUT = 20.0

# Official images, by what the repository declares. Pinned to a major only:
# the point is the right runtime, not a frozen patch release.
NODE_DEFAULT = "node:22-bookworm"
PYTHON_DEFAULT = "python:3.12-bookworm"

NVMRC = re.compile(r"v?(\d+)")
ENGINE = re.compile(r">=?\s*(\d+)")
PY_VERSION = re.compile(r"(\d+\.\d+)")


@dataclass
class Environment:
    """What the repository says it needs, and what will provide it."""
    image: str = ""
    why: str = ""
    available: bool = False
    reason: str = ""

    def render(self) -> str:
        if not self.available:
            return f"host ({self.reason})" if self.reason else "host"
        return f"{self.image} ({self.why})"


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        proc = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                              capture_output=True, text=True, timeout=15,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _read(repo: Path, rel: str) -> str:
    try:
        return (repo / rel).read_text("utf-8", errors="replace")
    except OSError:
        return ""


def _node_image(repo: Path) -> tuple[str, str] | None:
    if not (repo / "package.json").is_file():
        return None
    nvmrc = _read(repo, ".nvmrc").strip()
    m = NVMRC.search(nvmrc) if nvmrc else None
    if m:
        return f"node:{m.group(1)}-bookworm", ".nvmrc"
    try:
        engines = json.loads(_read(repo, "package.json")).get("engines") or {}
    except (json.JSONDecodeError, AttributeError):
        engines = {}
    m = ENGINE.search(str(engines.get("node", "")))
    if m:
        return f"node:{m.group(1)}-bookworm", "package.json engines.node"
    return NODE_DEFAULT, "package.json, no version pinned"


def _python_image(repo: Path) -> tuple[str, str] | None:
    pinned = _read(repo, ".python-version").strip()
    m = PY_VERSION.search(pinned) if pinned else None
    if m:
        return f"python:{m.group(1)}-bookworm", ".python-version"
    for name in ("pyproject.toml", "setup.cfg"):
        m = re.search(r"requires-python\s*=\s*['\"][>=~^\s]*(\d+\.\d+)",
                      _read(repo, name))
        if m:
            return f"python:{m.group(1)}-bookworm", name
    if any((repo / f).is_file() for f in
           ("pyproject.toml", "requirements.txt", "setup.py")):
        return PYTHON_DEFAULT, "python project, no version pinned"
    return None


def _devcontainer_image(repo: Path) -> tuple[str, str] | None:
    """A devcontainer that names an image outright. One that builds a
    Dockerfile is left alone -- that is a build, not a pull."""
    for rel in (".devcontainer/devcontainer.json", ".devcontainer.json"):
        raw = _read(repo, rel)
        if not raw:
            continue
        # Strip // comments: devcontainer.json is JSON-with-comments.
        cleaned = re.sub(r"^\s*//.*$", "", raw, flags=re.M)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            continue
        image = data.get("image")
        if isinstance(image, str) and image.strip():
            return image.strip(), rel
    return None


def host_satisfies(repo: Path, toolchain=None) -> str:
    """"" when the host can already build and test this repository.

    A container is worth its cost when the host cannot provide what the
    repository needs -- orca pins pnpm 12 and the host has no pnpm. It is
    NOT worth it when the host is already set up: moving a working Python
    project into a bare `python:3.12` image takes away the interpreter that
    had pytest and the project's dependencies installed, and the suite then
    yields nothing at all.

    So: the container is the answer to a missing tool, not the default.
    """
    import shutil

    if (repo / "package.json").is_file():
        for lock, tool in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"),
                           ("bun.lockb", "bun")):
            if (repo / lock).is_file() and not shutil.which(tool):
                if shutil.which("corepack") and tool != "bun":
                    continue          # corepack can provide it on the host
                return f"{tool} is not installed"
        if not shutil.which("node"):
            return "node is not installed"
        return ""

    if any((repo / f).is_file() for f in
           ("pyproject.toml", "requirements.txt", "setup.py", "tox.ini")):
        from .verify.toolchain import _host_python_interpreter
        framework = (getattr(toolchain, "framework", "") or "pytest")
        interp = _host_python_interpreter(repo, needs=framework)
        probe = subprocess.run([interp, "-c", f"import {framework}"],
                               capture_output=True, timeout=30,
                               stdin=subprocess.DEVNULL)
        if probe.returncode != 0:
            return f"{framework} is not importable by {interp}"
    return ""


def detect(repo: Path, toolchain=None) -> Environment:
    """Which image would run this repository's own commands."""
    override = (os.environ.get("HARNESS_DOCKER_IMAGE") or "").strip()
    if override:
        return Environment(image=override, why="HARNESS_DOCKER_IMAGE",
                           available=True)

    for finder in (_devcontainer_image, _node_image, _python_image):
        hit = finder(repo)
        if hit:
            return Environment(image=hit[0], why=hit[1], available=True)
    lang = (getattr(toolchain, "language", "") or "").lower()
    return Environment(reason=f"no image known for {lang or 'this repository'}")


def mode() -> str:
    """HARNESS_DOCKER: auto (default), always, off.

    `auto` uses a container when Docker is running and the repository says
    what it needs. `always` makes the absence of one a failure rather than a
    degradation -- for reproducing a result exactly.
    """
    raw = (os.environ.get("HARNESS_DOCKER") or "").strip().lower()
    if raw in ("off", "0", "no", "false", "never"):
        return "off"
    if raw in ("always", "1", "yes", "true", "on", "require"):
        return "always"
    return "auto"


@dataclass
class Container:
    """A running container with the repository mounted at /work."""
    repo: Path
    image: str
    name: str = field(default_factory=lambda: f"harness-{uuid.uuid4().hex[:12]}")
    started: bool = False

    def start(self, log=None) -> bool:
        """Never raises. Returns False and leaves the run on the host."""
        cmd = ["docker", "run", "--detach", "--rm",
               "--name", self.name,
               "--workdir", WORKDIR,
               "--volume", f"{self.repo.resolve()}:{WORKDIR}",
               # The repository's own code runs here. Keep it away from the
               # host's docker socket and give it a bounded amount of rope.
               "--memory", os.environ.get("HARNESS_DOCKER_MEMORY", "4g"),
               "--pids-limit", "2048",
               self.image, "sleep", "infinity"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=START_TIMEOUT,
                                  stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            if log:
                log.degraded("no_container", f"could not start: {exc}")
            return False
        if proc.returncode != 0:
            if log:
                log.degraded("no_container",
                             (proc.stderr or proc.stdout)[-200:].strip())
            return False
        self.started = True
        if log:
            log.ok("container", f"{self.image}",
                   plain=f"container: {self.image}")
        return True

    def translate(self, cwd) -> str:
        """A host path under the repository becomes its /work counterpart."""
        try:
            rel = Path(cwd).resolve().relative_to(self.repo.resolve())
        except (ValueError, OSError):
            return WORKDIR
        return WORKDIR if str(rel) == "." else f"{WORKDIR}/{rel}"

    def exec_argv(self, cmd: str, cwd, env: dict) -> list:
        argv = ["docker", "exec", "--workdir", self.translate(cwd)]
        # Only the variables the harness sets deliberately: the host's
        # environment is not the container's, and the scrubber has already
        # removed anything secret.
        for key in ("CI", "NO_COLOR", "PYTHONHASHSEED",
                    "PYTHONDONTWRITEBYTECODE", "LANG", "TERM"):
            if key in env:
                argv += ["--env", f"{key}={env[key]}"]
        for key, value in env.items():
            if key.startswith("HARNESS_TEST_"):
                argv += ["--env", f"{key}={value}"]
        argv += [self.name, "sh", "-lc", cmd]
        return argv

    def stop(self) -> None:
        if not self.started:
            return
        self.started = False
        try:
            subprocess.run(["docker", "rm", "--force", self.name],
                           capture_output=True, text=True,
                           timeout=STOP_TIMEOUT, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass          # a leaked container is untidy, not a failed run
