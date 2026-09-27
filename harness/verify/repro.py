"""Write the test the repository never had (SPEC.md 25.2, 26.6).

Most repositories have no suite. A harness that can only verify a fix when
somebody else already wrote the test is not autonomous -- it is waiting for
help that, in the normal case, never arrives.

So the harness writes the test itself. The problem with a self-written test
is obvious: a model asked to check its own work will happily write a test
that passes. The answer is **red-green**, and it is not optional here.

    1. write a test from the issue, before any fix exists;
    2. run it against the UNFIXED code -- it must FAIL;
    3. only then is it a reproduction, and only then is it kept;
    4. apply the fix;
    5. run it again -- it must PASS.

Step 2 is the whole design. A test that passes before the fix proves nothing
about the fix, and is rejected no matter how plausible it looks. That single
gate is what separates evidence from a model's opinion of itself, and it is
cheap: one command, and it is machine-checked rather than judged.

Nothing here needs a test framework to be installed. Both runtimes ship one:
`node --test` from Node 18, and `unittest` in the Python standard library.
A repository with no test tooling at all is still verifiable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .runner import run
from .toolchain import python_interpreter

# The file is written into the repository root, so the imports a model writes
# are the ordinary ones ("./src/lib/x.js"), not paths relative to some
# scratch directory it cannot see. The name is distinctive enough to exclude
# from the diff and to clean up without guessing.
STEM = "harness_repro"
NAMES = {
    "python": f"test_{STEM}.py",
    "javascript": f"{STEM}.test.js",
    "typescript": f"{STEM}.test.js",
}

MAX_ATTEMPTS = 2          # a rejected test is regenerated once, then dropped
TIMEOUT = 90.0

FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.S)

# Outcomes, in the order they are reached.
UNAVAILABLE = "unavailable"        # language has no runner here
GENERATION_FAILED = "generation_failed"
NOT_RED = "not_red"                # passed before the fix: proves nothing
VALID = "valid"                    # failed before the fix: a real oracle
PASSED = "passed"                  # and passes after it
STILL_FAILING = "still_failing"


@dataclass
class Repro:
    """A self-written reproduction and what it proved."""
    status: str = UNAVAILABLE
    path: Path | None = None
    cmd: str = ""
    source: str = ""
    attempts: int = 0
    detail: str = ""
    red_output: str = ""
    notes: list = field(default_factory=list)

    @property
    def is_oracle(self) -> bool:
        """True once the test has been shown to fail on the unfixed code."""
        return self.status in (VALID, PASSED, STILL_FAILING)

    @property
    def verified(self) -> bool:
        return self.status == PASSED

    def render(self) -> str:
        return {
            UNAVAILABLE: "no runner for this language",
            GENERATION_FAILED: "could not write a reproduction",
            NOT_RED: "written test passed before the fix -- rejected",
            VALID: "reproduction fails on the unfixed code",
            PASSED: "reproduction passes after the fix",
            STILL_FAILING: "reproduction still fails after the fix",
        }.get(self.status, self.status)


def command_for(repo: Path, language: str, path: Path) -> str:
    """How to run one test file with nothing installed.

    Preference is a real framework when the repository already has one,
    because its output parses at high confidence; the built-in runners are
    the fallback that makes a bare repository verifiable at all.
    """
    name = path.name
    if language in ("javascript", "typescript"):
        # node --test has been stable since Node 18 and needs no dependency.
        return f"node --test {name}"
    if language == "python":
        interp = python_interpreter(repo, needs="pytest")
        if interp and _can_import(repo, interp, "pytest"):
            return f"{interp} -m pytest {name} -q"
        interp = interp or python_interpreter(repo, needs="unittest")
        # unittest is stdlib, so this always runs something.
        return f"{interp} -m unittest -v {path.stem}"
    return ""


def _can_import(repo: Path, interp: str, module: str) -> bool:
    r = run(f"{interp} -c \"import {module}\"", repo, timeout=20,
            check_deny=False)
    return r.exit_code == 0


def _strip_fence(text: str) -> str:
    m = FENCE.search(text or "")
    body = m.group(1) if m else (text or "")
    return body.strip() + "\n"


def messages_for(issue: str, root_cause, language: str, filename: str,
                 context: str) -> list:
    """Ask for a test that fails now, and say so in those words.

    The instruction that matters is "it must fail against the code as it
    stands". Without it a model writes a test for the behaviour it is about
    to implement, which passes for the wrong reason or does not compile.
    """
    statement = getattr(root_cause, "statement", "") or ""
    runner = {
        "javascript": "Node's built-in test runner: "
                      "`import { test } from 'node:test'` and "
                      "`import assert from 'node:assert/strict'`. "
                      "Use ESM import syntax if the project is ESM, "
                      "otherwise require().",
        "typescript": "Node's built-in test runner (node:test).",
        "python": "pytest-style plain functions named test_*, using bare "
                  "assert. Do not import pytest.",
    }.get(language, "the language's standard test runner")

    system = (
        "You write a single regression test that reproduces a reported bug.\n"
        "\n"
        "Hard requirements:\n"
        f"1. The test MUST FAIL when run against the code exactly as it is "
        f"now. It is a reproduction of a bug that is still present. If you "
        f"write a test that passes, it is useless and will be discarded.\n"
        "2. Test the real behaviour through the real public API. Do not "
        "mock the thing you are testing, and do not reimplement it in the "
        "test.\n"
        "3. Assert the CORRECT expected behaviour, not the current buggy "
        "behaviour.\n"
        f"4. Use {runner}\n"
        "5. Import from the paths shown in the context, relative to the "
        "repository root, because the file is written there.\n"
        "6. Output one fenced code block containing the entire file, and "
        "nothing else. No explanation.\n"
    )
    user = (
        f"Repository language: {language}\n"
        f"The test file will be saved at the repository root as: {filename}\n\n"
        f"Issue:\n{issue.strip()[:4000]}\n\n"
        + (f"Diagnosed root cause:\n{statement[:1200]}\n\n" if statement else "")
        + f"Relevant code:\n{context[:8000]}\n"
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def install(repo: Path, language: str, source: str) -> Path | None:
    name = NAMES.get(language)
    if not name:
        return None
    path = repo / name
    path.write_text(source, encoding="utf-8")
    return path


def remove(repo: Path, language: str) -> None:
    """The reproduction is scaffolding, not a deliverable.

    It is deleted before the diff is taken, so it can never appear in the
    change set by accident -- committing a model-written test is a separate,
    explicit decision (§26.6).
    """
    name = NAMES.get(language)
    if not name:
        return
    try:
        (repo / name).unlink()
    except (OSError, FileNotFoundError):
        pass


def _looks_empty(result) -> bool:
    """A file that does not parse, or runs no test, is not a failing test.

    Both runners exit non-zero on a syntax error, which would otherwise read
    as "red" and be accepted as a reproduction of nothing.
    """
    text = (result.stdout or "") + (result.stderr or "")
    lowered = text.lower()
    broken = ("syntaxerror", "cannot find module", "modulenotfounderror",
              "importerror", "no such file", "cannot find package",
              "err_module_not_found", "referenceerror")
    if any(b in lowered for b in broken):
        return True
    if "# tests 0" in text or "no tests ran" in lowered:
        return True
    if "collected 0 items" in lowered:
        return True
    return False


def attempt(ctx, issue: str, root_cause, context: str) -> Repro:
    """Write a reproduction and prove it red. Never raises."""
    language = getattr(ctx.toolchain, "language", "") or ""
    name = NAMES.get(language)
    rep = Repro()
    if not name:
        rep.detail = f"no built-in runner for {language or 'this language'}"
        return rep

    path = ctx.repo / name
    cmd = command_for(ctx.repo, language, path)
    if not cmd:
        rep.detail = "no way to run a single test file"
        return rep
    rep.cmd = cmd

    feedback = ""
    for n in range(1, MAX_ATTEMPTS + 1):
        rep.attempts = n
        msgs = messages_for(issue, root_cause, language, name,
                            context + feedback)
        try:
            reply = ctx.router.call("repro_test", msgs, "P2", max_tokens=1200)
        except Exception as exc:                  # provider trouble is not fatal
            rep.status = GENERATION_FAILED
            rep.detail = f"model call failed: {exc}"
            return rep

        source = _strip_fence(reply.text)
        if not source.strip():
            rep.status = GENERATION_FAILED
            rep.detail = "empty reply"
            continue
        rep.source = source
        rep.path = install(ctx.repo, language, source)

        result = run(cmd, ctx.repo, timeout=TIMEOUT, check_deny=False)

        if _looks_empty(result):
            # Broken file: not evidence of anything. Try once more with the
            # error, which is the one piece of information that helps.
            feedback = (f"\n\nYour previous attempt did not run:\n"
                        f"{(result.stdout + result.stderr)[-1200:]}\n")
            rep.status = GENERATION_FAILED
            rep.detail = "written test did not run"
            remove(ctx.repo, language)
            rep.path = None
            continue

        if result.exit_code == 0:
            # It passes against the unfixed code, so it does not describe the
            # bug. This is the gate that makes the whole mechanism honest.
            rep.status = NOT_RED
            rep.detail = "passed before the fix"
            feedback = ("\n\nYour previous test PASSED against the current "
                        "code, so it does not reproduce the bug. Assert the "
                        "behaviour the issue says is missing or wrong.\n")
            remove(ctx.repo, language)
            rep.path = None
            continue

        rep.status = VALID
        rep.red_output = ((result.stdout or "") + (result.stderr or ""))[-2000:]
        rep.detail = "fails on the unfixed code"
        return rep

    return rep


def confirm(ctx, rep: Repro) -> Repro:
    """Run a validated reproduction against the fixed code."""
    if not rep.is_oracle or not rep.cmd:
        return rep
    result = run(rep.cmd, ctx.repo, timeout=TIMEOUT, check_deny=False)
    if _looks_empty(result):
        rep.status = STILL_FAILING
        rep.detail = "reproduction stopped running after the change"
        return rep
    rep.status = PASSED if result.exit_code == 0 else STILL_FAILING
    rep.detail = ("passes after the fix" if result.exit_code == 0
                  else "still fails after the fix")
    return rep
