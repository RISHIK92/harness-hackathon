"""Test and lint command discovery (SPEC.md 4.2, FR-27, FR-28).

CI workflows are the highest-priority source: they are what the project
itself considers "the tests".  We only ever run a linter the repository
already configures -- inventing `mypy` for a project that does not use it
manufactures blocking failures.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

TEST_HINTS = ("pytest", "tox", "unittest", "go test", "cargo test", "npm test",
              "yarn test", "pnpm test", "jest", "vitest", "mocha", "mvn test",
              "gradle test", "rspec", "phpunit", "make test")
LINT_HINTS = ("ruff", "flake8", "mypy", "pylint", "black --check", "eslint",
              "biome", "tsc --noemit", "go vet", "golangci-lint", "clippy",
              "rubocop", "phpcs", "make lint")


def _can_import(interpreter: str, module: str) -> bool:
    import subprocess
    try:
        r = subprocess.run([interpreter, "-c", f"import {module}"],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def python_interpreter(repo: Path, needs: str = "pytest") -> str:
    """Inside a container, host paths are meaningless -- a host virtualenv
    does not exist there, and binding one produces a command that cannot
    run."""
    from .runner import active_container
    if active_container() is not None:
        return "python3"
    return _host_python_interpreter(repo, needs)


def _host_python_interpreter(repo: Path, needs: str = "pytest") -> str:
    """The interpreter to actually invoke.

    Two failures this avoids:
      * "python" does not exist on a clean Ubuntu 24 (or on macOS), so a
        command of the form `python -m pytest` executes nothing at all;
      * an interpreter that cannot import the test framework runs zero tests
        and looks like a green suite.

    Preference: the repository's own virtualenv, then the interpreter running
    the harness, then python3/python on PATH.  The first candidate that can
    import `needs` wins; if none can, the first that exists is used so the
    failure is visible rather than silent.
    """
    candidates: list[str] = []
    for rel in (".venv/bin/python", "venv/bin/python", "env/bin/python",
                ".venv/Scripts/python.exe"):
        cand = repo / rel
        if cand.is_file():
            candidates.append(str(cand))
    candidates.append(sys.executable)
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)

    for cand in candidates:
        if _can_import(cand, needs):
            return cand
    return candidates[0] if candidates else sys.executable


@dataclass
class Toolchain:
    language: str = "unknown"
    test_cmd: str | None = None
    lint_cmd: str | None = None
    test_source: str = ""
    lint_source: str = ""
    framework: str = ""
    junit_flag: str | None = None
    coverage_prefix: str | None = None
    python: str = "python3"
    notes: list[str] = field(default_factory=list)

    @property
    def has_tests(self) -> bool:
        return bool(self.test_cmd)

    @property
    def has_lint(self) -> bool:
        return bool(self.lint_cmd)


def _read(path: Path, limit: int = 60_000) -> str:
    try:
        return path.read_text("utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _ci_commands(repo: Path) -> list[str]:
    """`run:` steps from CI workflows -- the authoritative source."""
    cmds: list[str] = []
    for pattern in (".github/workflows/*.yml", ".github/workflows/*.yaml",
                    ".gitlab-ci.yml", ".circleci/config.yml"):
        for f in sorted(repo.glob(pattern)):
            body = _read(f)
            for m in re.finditer(r"^\s*-?\s*run:\s*(\|[-+]?\s*)?(.*)$",
                                 body, re.M):
                inline = (m.group(2) or "").strip()
                if inline:
                    cmds.append(inline)
                    continue
                start = body.index(m.group(0)) + len(m.group(0))
                for line in body[start:].splitlines()[:12]:
                    if line.strip() and (line.startswith(" ") or
                                         line.startswith("\t")):
                        cmds.append(line.strip())
                    elif line.strip():
                        break
    return cmds


def _make_targets(repo: Path) -> set[str]:
    body = _read(repo / "Makefile")
    return {m.group(1) for m in re.finditer(r"^([A-Za-z0-9_.-]+):", body, re.M)}


def _pick(cmds: list[str], hints: tuple) -> str | None:
    """The most canonical command that matches, not the first one seen.

    A CI workflow holds many specialised invocations. Taking the first hit
    chose `pnpm test:bun:profile --artifact` -- a profiling run -- over the
    plain `test` script the repository actually defines. The suite has to be
    the ordinary one, or every verdict is about the wrong thing.
    """
    best, best_score = None, None
    for c in cmds:
        low = c.lower()
        if not any(h in low for h in hints):
            continue
        if any(bad in low for bad in ("install", "upgrade", "checkout",
                                      "setup-python", "actions/")):
            continue
        # Lower is better: a script name qualified with ":" is a variant, and
        # extra flags mean a special mode rather than "run the tests".
        score = (low.count(":"), low.count("--"), len(low))
        if best_score is None or score < best_score:
            best, best_score = c, score
    return best


def discover_toolchain(repo: Path, cfg=None) -> Toolchain:
    repo = Path(repo)
    tc = Toolchain()

    # 0. explicit overrides win outright
    if cfg is not None and getattr(cfg, "test_cmd", None):
        tc.test_cmd, tc.test_source = cfg.test_cmd, "HARNESS_TEST_CMD"
    if cfg is not None and getattr(cfg, "lint_cmd", None):
        tc.lint_cmd, tc.lint_source = cfg.lint_cmd, "HARNESS_LINT_CMD"

    # 1. language + framework from manifests
    tc.language, tc.framework = _language(repo)

    # 2. CI workflows
    ci = _ci_commands(repo)
    if not tc.test_cmd:
        found = _pick(ci, TEST_HINTS)
        if found:
            tc.test_cmd, tc.test_source = found, "ci workflow"
    if not tc.lint_cmd:
        found = _pick(ci, LINT_HINTS)
        if found:
            tc.lint_cmd, tc.lint_source = found, "ci workflow"

    # 3. Makefile / package.json scripts
    targets = _make_targets(repo)
    if not tc.test_cmd and {"test", "check"} & targets:
        t = "test" if "test" in targets else "check"
        tc.test_cmd, tc.test_source = f"make {t}", "Makefile"
    if not tc.lint_cmd and "lint" in targets:
        tc.lint_cmd, tc.lint_source = "make lint", "Makefile"

    pkg = repo / "package.json"
    if pkg.is_file():
        try:
            scripts = json.loads(_read(pkg)).get("scripts", {}) or {}
        except json.JSONDecodeError:
            scripts = {}
        if not tc.test_cmd and "test" in scripts:
            tc.test_cmd, tc.test_source = "npm test --silent", "package.json"
        if not tc.lint_cmd and "lint" in scripts:
            tc.lint_cmd, tc.lint_source = "npm run lint --silent", "package.json"

    # 4. language defaults
    _defaults(repo, tc)

    # A command naming a package manager nobody installed never runs. The
    # repository pins one; corepack ships with Node to provide exactly that.
    tc.test_cmd = _with_runner(tc.test_cmd)
    tc.lint_cmd = _with_runner(tc.lint_cmd)

    # 5. bind a real interpreter -- "python" may not exist on the eval machine
    if tc.language == "python":
        tc.python = python_interpreter(repo)
        tc.test_cmd = _bind_python(tc.test_cmd, tc.python)
        tc.lint_cmd = _bind_python(tc.lint_cmd, tc.python)
    return tc


def _bind_python(cmd: str | None, interpreter: str) -> str | None:
    if not cmd:
        return cmd
    for prefix in ("python3 -m ", "python -m "):
        if cmd.startswith(prefix):
            return f"{interpreter} -m " + cmd[len(prefix):]
    if cmd.startswith("pytest "):
        return f"{interpreter} -m {cmd}"
    if cmd == "pytest":
        return f"{interpreter} -m pytest"
    return cmd


def _language(repo: Path) -> tuple[str, str]:
    if (repo / "go.mod").is_file():
        return "go", "gotest"
    if (repo / "Cargo.toml").is_file():
        return "rust", "cargo"
    if (repo / "pom.xml").is_file():
        return "java", "maven"
    if any((repo / f).is_file() for f in ("build.gradle", "build.gradle.kts")):
        return "java", "gradle"
    if (repo / "Gemfile").is_file():
        return "ruby", "rspec"
    if (repo / "composer.json").is_file():
        return "php", "phpunit"
    if (repo / "package.json").is_file():
        body = _read(repo / "package.json")
        # An empty framework means "none found", which is a fact worth
        # reporting. Calling it "node" turns it into `npx node` -- a REPL
        # that waits on stdin forever instead of running any test.
        fw = ("vitest" if "vitest" in body else
              "jest" if "jest" in body else
              "mocha" if "mocha" in body else
              "tap" if '"tap"' in body else
              "ava" if '"ava"' in body else "")
        lang = "typescript" if (repo / "tsconfig.json").is_file() else "javascript"
        return lang, fw
    if any((repo / f).is_file() for f in ("pyproject.toml", "setup.py",
                                          "setup.cfg", "tox.ini",
                                          "pytest.ini", "requirements.txt")):
        return "python", "pytest"
    if list(repo.glob("**/*.py"))[:1]:
        return "python", "pytest"
    return "unknown", ""


def _with_runner(cmd: str | None) -> str | None:
    """Rewrite a leading `pnpm`/`yarn` to `corepack <tool>` when missing."""
    if not cmd:
        return cmd
    head = cmd.split(None, 1)[0]
    if head not in ("pnpm", "yarn", "bun"):
        return cmd
    import shutil
    if shutil.which(head) or not shutil.which("corepack"):
        return cmd
    return f"corepack {cmd}"


def _defaults(repo: Path, tc: Toolchain) -> None:
    lang = tc.language

    if lang == "python":
        if not tc.test_cmd:
            tc.test_cmd, tc.test_source = "python -m pytest -q", "python default"
        tc.junit_flag = "--junitxml={path}"
        tc.coverage_prefix = "python -m coverage run -m"
        if not tc.lint_cmd:
            cfgs = _read(repo / "pyproject.toml") + _read(repo / "setup.cfg") \
                + _read(repo / "tox.ini")
            if "[tool.ruff" in cfgs or (repo / ".ruff.toml").is_file():
                tc.lint_cmd, tc.lint_source = "python -m ruff check", "pyproject"
            elif "[flake8]" in cfgs or (repo / ".flake8").is_file():
                tc.lint_cmd, tc.lint_source = "python -m flake8", "config"
            elif "[tool.mypy" in cfgs or (repo / "mypy.ini").is_file():
                tc.lint_cmd, tc.lint_source = "python -m mypy .", "config"
            else:
                tc.notes.append("no linter configured by the repository")

    elif lang == "go":
        tc.test_cmd = tc.test_cmd or "go test ./..."
        tc.test_source = tc.test_source or "go default"
        tc.junit_flag = "-json"
        if not tc.lint_cmd:
            tc.lint_cmd, tc.lint_source = "go vet ./...", "go default"

    elif lang == "rust":
        tc.test_cmd = tc.test_cmd or "cargo test"
        tc.test_source = tc.test_source or "cargo default"
        if not tc.lint_cmd and (repo / "clippy.toml").is_file():
            tc.lint_cmd, tc.lint_source = "cargo clippy", "config"

    elif lang in ("javascript", "typescript"):
        if not tc.test_cmd and tc.framework:
            tc.test_cmd = f"npx {tc.framework} --run" \
                if tc.framework == "vitest" else f"npx {tc.framework}"
            tc.test_source = "devDependency"
        if not tc.lint_cmd:
            if (repo / ".eslintrc.json").is_file() or \
               (repo / "eslint.config.js").is_file():
                tc.lint_cmd, tc.lint_source = "npx eslint .", "config"
            elif (repo / "tsconfig.json").is_file():
                tc.lint_cmd, tc.lint_source = "npx tsc --noEmit", "tsconfig"

    elif lang == "java":
        tc.test_cmd = tc.test_cmd or (
            "mvn -q test" if tc.framework == "maven" else "./gradlew test")
        tc.test_source = tc.test_source or "build file"

    if not tc.test_cmd:
        tc.notes.append("no test command discovered")
