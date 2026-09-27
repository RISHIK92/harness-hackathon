"""The harness writes its own test when the repository has none.

The point of these tests is the red gate. A self-written test is only
evidence if it was shown to fail before the fix existed; everything else here
follows from that.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.verify import repro as R                       # noqa: E402

HAS_NODE = shutil.which("node") is not None


class Reply:
    def __init__(self, text):
        self.text = text


class Router:
    """Hands back scripted replies, and counts how often it was asked."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def call(self, *a, **k):
        self.calls += 1
        return Reply(self.replies.pop(0) if self.replies else "")


class Toolchain:
    def __init__(self, language, test_cmd=None):
        self.language = language
        self.test_cmd = test_cmd


class Ctx:
    def __init__(self, repo, router, language="python"):
        self.repo = repo
        self.router = router
        self.toolchain = Toolchain(language)


@pytest.fixture
def pyrepo(tmp_path):
    (tmp_path / "calc.py").write_text(
        "def grand_total(items, discount=0.0):\n"
        "    return sum(i['price'] * i['qty'] for i in items) - discount\n")
    return tmp_path


REPRODUCES = """```python
from calc import grand_total

def test_negative_discount_is_rejected():
    try:
        grand_total([{"price": 10, "qty": 2}], discount=-5)
    except ValueError:
        return
    assert False, "a negative discount should be rejected"
```"""

PASSES_ALREADY = """```python
from calc import grand_total

def test_totals_add_up():
    assert grand_total([{"price": 10, "qty": 2}]) == 20
```"""

BROKEN = """```python
from calc import grand_total
def test_oops(:
```"""


def test_a_test_that_fails_first_is_accepted_as_an_oracle(pyrepo):
    rep = R.attempt(Ctx(pyrepo, Router(REPRODUCES)), "negative discounts "
                    "are accepted", None, "calc.py")
    assert rep.status == R.VALID
    assert rep.is_oracle
    assert not rep.verified, "not verified until it passes after the fix"
    assert rep.attempts == 1


def test_a_test_that_already_passes_is_rejected(pyrepo):
    """The gate that makes this honest: passing before the fix proves
    nothing, however plausible the test looks."""
    router = Router(PASSES_ALREADY, PASSES_ALREADY)
    rep = R.attempt(Ctx(pyrepo, router), "something is wrong", None, "calc.py")

    assert rep.status == R.NOT_RED
    assert not rep.is_oracle, "a green test must never become the oracle"
    assert router.calls == 2, "a rejected test should be retried once"
    assert not (pyrepo / R.NAMES["python"]).exists(), "left a file behind"


def test_a_rejected_test_can_be_replaced_by_a_good_one(pyrepo):
    router = Router(PASSES_ALREADY, REPRODUCES)
    rep = R.attempt(Ctx(pyrepo, router), "negative discounts", None, "calc.py")
    assert rep.status == R.VALID
    assert rep.attempts == 2


def test_a_test_that_does_not_even_run_is_not_treated_as_red(pyrepo):
    """A syntax error also exits non-zero. Without this check it would be
    accepted as a reproduction of nothing."""
    rep = R.attempt(Ctx(pyrepo, Router(BROKEN, BROKEN)), "x", None, "calc.py")
    assert rep.status == R.GENERATION_FAILED
    assert not rep.is_oracle


def test_the_full_red_then_green_cycle(pyrepo):
    ctx = Ctx(pyrepo, Router(REPRODUCES))
    rep = R.attempt(ctx, "negative discounts are accepted", None, "calc.py")
    assert rep.status == R.VALID

    (pyrepo / "calc.py").write_text(
        "def grand_total(items, discount=0.0):\n"
        "    if discount < 0:\n"
        "        raise ValueError('negative discount')\n"
        "    return sum(i['price'] * i['qty'] for i in items) - discount\n")

    rep = R.confirm(ctx, rep)
    assert rep.status == R.PASSED
    assert rep.verified


def test_a_fix_that_does_not_work_leaves_the_reproduction_red(pyrepo):
    ctx = Ctx(pyrepo, Router(REPRODUCES))
    rep = R.attempt(ctx, "negative discounts", None, "calc.py")

    # A "fix" that changes something irrelevant.
    (pyrepo / "calc.py").write_text(
        "def grand_total(items, discount=0.0):\n"
        "    # tidied up\n"
        "    return sum(i['price'] * i['qty'] for i in items) - discount\n")

    rep = R.confirm(ctx, rep)
    assert rep.status == R.STILL_FAILING
    assert not rep.verified


def test_cleanup_removes_the_file(pyrepo):
    ctx = Ctx(pyrepo, Router(REPRODUCES))
    R.attempt(ctx, "x", None, "calc.py")
    assert (pyrepo / R.NAMES["python"]).exists()
    R.remove(pyrepo, "python")
    assert not (pyrepo / R.NAMES["python"]).exists()


@pytest.mark.skipif(not HAS_NODE, reason="node is not installed")
def test_javascript_needs_no_test_framework_installed(tmp_path):
    """`node --test` is built in, so a bare repo is still verifiable."""
    (tmp_path / "package.json").write_text('{"name":"x","type":"module"}')
    (tmp_path / "lib.js").write_text(
        "export function bucket(p) { return p < 50 ? 'low' : 'high'; }\n")

    reply = """```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bucket } from './lib.js';
test('90 and above is complete', () => {
  assert.equal(bucket(95), 'complete');
});
```"""
    ctx = Ctx(tmp_path, Router(reply), language="javascript")
    rep = R.attempt(ctx, "finished items are mislabelled", None, "lib.js")
    assert rep.status == R.VALID
    assert rep.cmd == "node --test harness_repro.test.js"

    (tmp_path / "lib.js").write_text(
        "export function bucket(p) {\n"
        "  if (p >= 90) return 'complete';\n"
        "  return p < 50 ? 'low' : 'high';\n}\n")
    assert R.confirm(ctx, rep).verified


def test_the_reproduction_never_counts_as_a_changed_file(tmp_path):
    """It lives at the repository root, so without an exclusion it would
    fail C4 and put a model-written test into the diff."""
    from harness.repo.workspace import Workspace

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=tmp_path, check=True,
                   stdin=subprocess.DEVNULL)

    (tmp_path / "a.py").write_text("x = 2\n")
    (tmp_path / R.NAMES["python"]).write_text("def test_x(): assert True\n")

    changed = Workspace(tmp_path, None).changed_files()
    assert "a.py" in changed
    assert R.NAMES["python"] not in changed, \
        "the self-written test reached the change set"


# -- how confidence uses it ------------------------------------------------

class _Verify:
    def __init__(self, ran=False, blocking=()):
        self.full = object() if ran else None
        self.scoped = None
        self.blocking = list(blocking)
        self.oracle_passes = None


class _Repro:
    def __init__(self, status):
        self.status = status

    @property
    def is_oracle(self):
        return self.status in (R.VALID, R.PASSED, R.STILL_FAILING)

    @property
    def verified(self):
        return self.status == R.PASSED


def _c3(verify, repro):
    """C3 as p5 computes it, isolated from the other conditions."""
    ran = bool(verify.full or verify.scoped)
    value = ran and not verify.blocking and verify.oracle_passes is not False
    if repro is not None and repro.is_oracle:
        if not ran:
            value = repro.verified
        elif not repro.verified:
            value = False
    return value


def test_c3_without_a_suite_needs_a_proven_reproduction():
    assert _c3(_Verify(ran=False), None) is False, "no evidence, no pass"
    assert _c3(_Verify(ran=False), _Repro(R.PASSED)) is True
    assert _c3(_Verify(ran=False), _Repro(R.STILL_FAILING)) is False
    assert _c3(_Verify(ran=False), _Repro(R.NOT_RED)) is False, \
        "a rejected test must not count as evidence"


def test_a_red_reproduction_overrides_a_green_suite():
    """If our own test still fails, the fix did not work -- whatever else
    passed."""
    assert _c3(_Verify(ran=True), _Repro(R.STILL_FAILING)) is False
    assert _c3(_Verify(ran=True), _Repro(R.PASSED)) is True
