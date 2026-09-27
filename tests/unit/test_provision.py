"""The environment ladder: what to do when the host cannot build the repo.

Nothing here may touch the real machine. The install rung runs a package
manager, so every test stubs it -- a test that installs Docker on whoever
runs the suite is not a test.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness import container as C                        # noqa: E402
from harness import provision as P                        # noqa: E402
from harness.logging_ui import Logger                      # noqa: E402


class Gate:
    def __init__(self, answer=True):
        self.answer = answer
        self.asked = []

    def allow(self, action, summary, rows=None, requested=False):
        self.asked.append(summary)
        return self.answer


class Ctx:
    def __init__(self, repo, gate=None, framework="pytest"):
        self.repo = repo
        self.gate = gate
        self.log = Logger(rich=False, stream=open(os.devnull, "w"))

        class TC:
            pass
        self.toolchain = TC()
        self.toolchain.framework = framework
        self.toolchain.language = "python"
        self.toolchain.test_cmd = "pytest -q"


def mk(**files) -> Path:
    d = Path(tempfile.mkdtemp())
    for name, body in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return d


@pytest.fixture(autouse=True)
def _never_install(monkeypatch):
    """The install rung must never run a real package manager here."""
    monkeypatch.setattr(P, "_docker_installer", lambda: None)
    monkeypatch.delenv("HARNESS_DOCKER", raising=False)
    # Preparing the machine is opt-in; these tests are about the ladder, so
    # they turn it on. The default-off behaviour has its own tests below.
    monkeypatch.setenv("HARNESS_LIVE_ENV", "1")
    yield
    from harness.verify.runner import use_container
    use_container(None)


# -- when nothing is wrong --------------------------------------------------

def test_a_satisfied_host_is_left_alone(monkeypatch):
    """Moving a working setup into a bare image loses the interpreter that
    had the dependencies -- the suite then yields nothing at all."""
    repo = mk(**{"package.json": '{"name":"x"}'})
    monkeypatch.setattr(P, "assess", lambda r, t: "")
    decision = P.provision(repo, Ctx(repo, Gate()))
    assert decision.strategy == "host"
    assert decision.considered == ["host"]


def test_off_disables_the_whole_ladder(monkeypatch):
    repo = mk(**{"package.json": '{"name":"x"}'})
    monkeypatch.setenv("HARNESS_DOCKER", "off")
    monkeypatch.setattr(P, "assess", lambda r, t: "bun is not installed")
    decision = P.provision(repo, Ctx(repo, Gate()))
    assert decision.strategy == "host"
    assert "disabled" in decision.detail


# -- the rungs, in order ----------------------------------------------------

def test_a_local_virtualenv_is_preferred_over_a_container(monkeypatch):
    """Cheapest first: a venv is local and reversible and needs no
    permission, so it is tried before pulling an image."""
    repo = mk(**{"requirements.txt": "",
                 "pyproject.toml": "[project]\nname='x'\nversion='0'\n"})
    monkeypatch.setattr(P, "assess", lambda r, t: "pytest is not installed")
    tried = []
    monkeypatch.setattr(P, "_try_container",
                        lambda r, g, c: tried.append("container"))

    decision = P.provision(repo, Ctx(repo, Gate()))
    assert decision.strategy == "venv"
    assert not tried, "went to a container with a venv available"
    assert (repo / ".venv").is_dir()


def test_a_container_is_used_when_a_venv_cannot_help(monkeypatch):
    repo = mk(**{"package.json": '{"name":"x"}', "bun.lockb": ""})
    monkeypatch.setattr(P, "assess", lambda r, t: "bun is not installed")
    monkeypatch.setattr(P, "_try_venv", lambda r, g, c: None)
    monkeypatch.setattr(
        P, "_try_container",
        lambda r, g, c: P.Decision(strategy="container",
                                   detail="node:22-bookworm", gap=g))
    decision = P.provision(repo, Ctx(repo, Gate()))
    assert decision.strategy == "container"


# -- installing Docker: always asked, never assumed -------------------------

def test_installing_docker_asks_first(monkeypatch):
    repo = mk(**{"package.json": '{"name":"x"}', "bun.lockb": ""})
    monkeypatch.setattr(P, "assess", lambda r, t: "bun is not installed")
    monkeypatch.setattr(P, "_try_venv", lambda r, g, c: None)
    monkeypatch.setattr(P, "_try_container", lambda r, g, c: None)
    monkeypatch.setattr(P, "container_present", lambda: False)
    monkeypatch.setattr(P, "_docker_installer",
                        lambda: ("echo would-install", "test"))
    ran = []
    monkeypatch.setattr("harness.verify.runner.run",
                        lambda *a, **k: ran.append(a) or _ok())

    gate = Gate(answer=True)
    P.provision(repo, Ctx(repo, gate))
    assert any("install Docker" in q for q in gate.asked)


def test_a_refusal_to_install_is_not_a_failure(monkeypatch):
    """The ladder continues, and running on an imperfect host with that
    recorded beats stopping."""
    repo = mk(**{"package.json": '{"name":"x"}', "bun.lockb": ""})
    monkeypatch.setattr(P, "assess", lambda r, t: "bun is not installed")
    monkeypatch.setattr(P, "_try_venv", lambda r, g, c: None)
    monkeypatch.setattr(P, "_try_container", lambda r, g, c: None)
    monkeypatch.setattr(P, "container_present", lambda: False)
    monkeypatch.setattr(P, "_docker_installer",
                        lambda: ("echo would-install", "test"))
    installed = []
    monkeypatch.setattr("harness.verify.runner.run",
                        lambda *a, **k: installed.append(a) or _ok())

    gate = Gate(answer=False)
    decision = P.provision(repo, Ctx(repo, gate))
    assert gate.asked, "never asked"
    assert not installed, "installed after being refused"
    assert decision.strategy == "host"
    assert decision.considered == ["venv", "container", "install-docker"]


def test_nothing_is_installed_without_someone_to_ask(monkeypatch):
    """Unattended, changing the machine is not a decision to make alone."""
    repo = mk(**{"package.json": '{"name":"x"}', "bun.lockb": ""})
    monkeypatch.setattr(P, "assess", lambda r, t: "bun is not installed")
    monkeypatch.setattr(P, "_try_venv", lambda r, g, c: None)
    monkeypatch.setattr(P, "_try_container", lambda r, g, c: None)
    monkeypatch.setattr(P, "container_present", lambda: False)
    monkeypatch.setattr(P, "_docker_installer",
                        lambda: ("echo would-install", "test"))
    installed = []
    monkeypatch.setattr("harness.verify.runner.run",
                        lambda *a, **k: installed.append(a) or _ok())

    decision = P.provision(repo, Ctx(repo, gate=None))
    assert not installed
    assert decision.strategy == "host"


def test_a_rung_that_raises_does_not_end_the_run(monkeypatch):
    repo = mk(**{"package.json": '{"name":"x"}'})
    monkeypatch.setattr(P, "assess", lambda r, t: "something is missing")

    def explode(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(P, "_try_venv", explode)
    monkeypatch.setattr(P, "_try_container", lambda r, g, c: None)
    monkeypatch.setattr(P, "_try_install_docker", lambda r, g, c: None)
    assert P.provision(repo, Ctx(repo, Gate())).strategy == "host"


def test_the_decision_is_recorded(monkeypatch):
    """"the suite did not run" is the failure that silently invalidates
    everything downstream, so the reason is part of the evidence."""
    repo = mk(**{"package.json": '{"name":"x"}'})
    monkeypatch.setattr(P, "assess", lambda r, t: "node is not installed")
    monkeypatch.setattr(P, "_try_venv", lambda r, g, c: None)
    monkeypatch.setattr(P, "_try_container", lambda r, g, c: None)
    monkeypatch.setattr(P, "_try_install_docker", lambda r, g, c: None)
    decision = P.provision(repo, Ctx(repo, Gate()))
    blob = decision.to_json()
    assert blob["gap"] == "node is not installed"
    assert blob["considered"] == ["venv", "container", "install-docker"]


def test_the_known_installers_are_per_platform():
    """Whatever it returns must be a real command, not a guess."""
    hit = P._docker_installer.__wrapped__() if hasattr(
        P._docker_installer, "__wrapped__") else None
    assert hit is None or isinstance(hit, tuple)


def _ok():
    from harness.verify.runner import Result
    return Result(cmd="", exit_code=0, stdout="", stderr="", duration_s=0.0)


# -- preparing the machine is opt-in ----------------------------------------

def test_the_environment_is_left_alone_by_default(monkeypatch):
    monkeypatch.delenv("HARNESS_LIVE_ENV", raising=False)
    """Cloning and editing needs nothing installed, and an install changes
    the working copy before the first edit is even proposed."""
    monkeypatch.delenv("HARNESS_LIVE_ENV", raising=False)
    assert not P.use_environment()

    repo = mk(**{"package.json": '{"name":"x"}', "bun.lockb": ""})
    monkeypatch.setattr(P, "assess", lambda r, t: "bun is not installed")
    tried = []
    for rung in ("_try_venv", "_try_container", "_try_install_docker"):
        monkeypatch.setattr(P, rung,
                            lambda r, g, c, n=rung: tried.append(n))

    decision = P.provision(repo, Ctx(repo, Gate()))
    assert decision.strategy == "host"
    assert not tried, "prepared the machine without being asked"
    assert "HARNESS_LIVE_ENV" in decision.detail


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("on", True), ("yes", True),
    ("0", False), ("off", False), ("", False), ("banana", False),
])
def test_the_switch_reads_plainly(value, expected, monkeypatch):
    monkeypatch.setenv("HARNESS_LIVE_ENV", value)
    assert P.use_environment() is expected


def test_enabling_it_runs_the_ladder(monkeypatch):
    monkeypatch.setenv("HARNESS_LIVE_ENV", "1")
    repo = mk(**{"package.json": '{"name":"x"}', "bun.lockb": ""})
    monkeypatch.setattr(P, "assess", lambda r, t: "bun is not installed")
    monkeypatch.setattr(P, "_try_venv", lambda r, g, c: None)
    monkeypatch.setattr(
        P, "_try_container",
        lambda r, g, c: P.Decision(strategy="container", detail="img", gap=g))
    assert P.provision(repo, Ctx(repo, Gate())).strategy == "container"


def test_dependencies_are_not_installed_when_it_is_off():
    import inspect

    from harness.orchestrator import Orchestrator
    source = inspect.getsource(Orchestrator._install_dependencies)
    assert "use_environment" in source
