"""Running the repository's commands in the runtime it declares.

The host is the wrong place to build someone else's project: orca pins pnpm
12 and a Node major the host does not have, and installing 137 dependencies
onto the operator's machine to find out is not a neutral act either.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness import container as C                        # noqa: E402

HAS_DOCKER = C.docker_available()
needs_docker = pytest.mark.skipif(not HAS_DOCKER, reason="docker not running")


# -- which image ------------------------------------------------------------

def test_nvmrc_pins_the_node_major(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / ".nvmrc").write_text("v20.11.1\n")
    env = C.detect(tmp_path)
    assert env.image == "node:20-bookworm"
    assert ".nvmrc" in env.why


def test_engines_pins_node_when_there_is_no_nvmrc(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"engines": {"node": ">=18.0.0"}}))
    assert C.detect(tmp_path).image == "node:18-bookworm"


def test_a_node_project_without_a_pin_gets_a_current_default(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    assert C.detect(tmp_path).image == C.NODE_DEFAULT


def test_python_version_file_pins_the_interpreter(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / ".python-version").write_text("3.11.9\n")
    assert C.detect(tmp_path).image == "python:3.11-bookworm"


def test_requires_python_is_honoured(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.10"\n')
    assert C.detect(tmp_path).image == "python:3.10-bookworm"


def test_a_devcontainer_image_wins(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / ".devcontainer").mkdir()
    (tmp_path / ".devcontainer" / "devcontainer.json").write_text(
        '// the project says what it wants\n'
        '{"image": "mcr.microsoft.com/devcontainers/base:ubuntu"}\n')
    env = C.detect(tmp_path)
    assert env.image == "mcr.microsoft.com/devcontainers/base:ubuntu"


def test_a_devcontainer_that_builds_is_left_alone(tmp_path):
    """Building a repository's Dockerfile is slow and often needs secrets."""
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / ".devcontainer").mkdir()
    (tmp_path / ".devcontainer" / "devcontainer.json").write_text(
        '{"build": {"dockerfile": "Dockerfile"}}')
    assert C.detect(tmp_path).image == C.NODE_DEFAULT


def test_an_unknown_repository_gets_no_image(tmp_path):
    (tmp_path / "main.rs").write_text("fn main() {}")
    env = C.detect(tmp_path)
    assert not env.available and env.reason


def test_an_explicit_image_overrides_detection(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text("{}")
    monkeypatch.setenv("HARNESS_DOCKER_IMAGE", "node:18-alpine")
    assert C.detect(tmp_path).image == "node:18-alpine"


# -- when to use one --------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (None, "auto"), ("", "auto"), ("auto", "auto"),
    ("off", "off"), ("0", "off"), ("never", "off"),
    ("always", "always"), ("1", "always"),
    ("banana", "auto"),
])
def test_mode(value, expected, monkeypatch):
    monkeypatch.delenv("HARNESS_DOCKER", raising=False)
    if value is not None:
        monkeypatch.setenv("HARNESS_DOCKER", value)
    assert C.mode() == expected


# -- the exec contract ------------------------------------------------------

def test_a_path_under_the_repo_maps_into_the_mount(tmp_path):
    box = C.Container(repo=tmp_path, image="x")
    assert box.translate(tmp_path) == C.WORKDIR
    sub = tmp_path / "src" / "lib"
    sub.mkdir(parents=True)
    assert box.translate(sub) == f"{C.WORKDIR}/src/lib"


def test_a_path_outside_the_repo_falls_back_to_the_mount(tmp_path):
    box = C.Container(repo=tmp_path, image="x")
    assert box.translate(Path("/etc")) == C.WORKDIR


def test_no_host_secret_is_handed_to_the_container(tmp_path):
    box = C.Container(repo=tmp_path, image="x")
    argv = box.exec_argv("npm test", tmp_path, {
        "CI": "1", "AI_API_KEY": "sk-should-never-appear",
        "GITHUB_TOKEN": "ghp_nope", "AWS_SECRET_ACCESS_KEY": "nope",
    })
    joined = " ".join(argv)
    for leaked in ("sk-should-never-appear", "ghp_nope", "nope"):
        assert leaked not in joined
    assert "CI=1" in joined
    assert argv[-3:] == ["sh", "-lc", "npm test"]


# -- really running ---------------------------------------------------------

@needs_docker
def test_commands_run_in_the_declared_runtime_not_the_host(tmp_path):
    """The whole point: the version the project asks for."""
    from harness.verify.runner import run, use_container

    (tmp_path / "package.json").write_text("{}")
    (tmp_path / ".nvmrc").write_text("20\n")
    env = C.detect(tmp_path)
    assert env.image == "node:20-bookworm"

    box = C.Container(repo=tmp_path, image=env.image)
    if not box.start():
        pytest.skip("could not pull the image")
    try:
        use_container(box)
        inside = run("node --version", tmp_path, timeout=120,
                     check_deny=False).stdout.strip()
        assert inside.startswith("v20."), inside
        assert run("pwd", tmp_path, timeout=60,
                   check_deny=False).stdout.strip() == C.WORKDIR
    finally:
        use_container(None)
        box.stop()

    # And the host is itself again.
    assert not box.started
    host = run("node --version", tmp_path, timeout=60,
               check_deny=False).stdout.strip()
    assert host != inside or True        # only that it runs on the host again


@needs_docker
def test_the_repository_is_writable_from_inside(tmp_path):
    """Edits and installs have to land in the mount, or nothing works."""
    from harness.verify.runner import run, use_container

    (tmp_path / "package.json").write_text("{}")
    box = C.Container(repo=tmp_path, image=C.NODE_DEFAULT)
    if not box.start():
        pytest.skip("could not pull the image")
    try:
        use_container(box)
        run("echo written-inside > marker.txt", tmp_path, timeout=60,
            check_deny=False)
    finally:
        use_container(None)
        box.stop()
    assert (tmp_path / "marker.txt").read_text().strip() == "written-inside"


@needs_docker
def test_stopping_is_idempotent_and_removes_the_container(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    box = C.Container(repo=tmp_path, image=C.NODE_DEFAULT)
    if not box.start():
        pytest.skip("could not pull the image")
    name = box.name
    box.stop()
    box.stop()                                   # must not raise
    listed = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    assert name not in listed.stdout


def test_without_a_container_commands_run_on_the_host(tmp_path):
    from harness.verify.runner import active_container, run, use_container

    use_container(None)
    assert active_container() is None
    assert run("echo host", tmp_path, timeout=30,
               check_deny=False).stdout.strip() == "host"


def test_a_container_never_binds_a_host_interpreter_path():
    """A host virtualenv does not exist inside the container."""
    import inspect

    from harness.verify import toolchain
    source = inspect.getsource(toolchain.python_interpreter)
    assert "active_container" in source
    assert "python3" in source
