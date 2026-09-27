"""Supervising a call instead of guessing its budget up front.

From a real run against deepseek-v4-pro on stablyai/orca#23250:

    p3_implement  1.2k -> 3.0k  55.7s   -> empty reply
    p3_implement  1.2k -> 3.0k  51.5s   -> empty reply
    p3_implement  1.2k -> 3.0k  replayed -> empty reply

`-> 3.0k` is the fixed ceiling, hit every time. A reasoning model spends its
output budget thinking, so it was cut off before writing the patch -- and the
cache then re-served that truncated failure to the next cycle.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.watcher import CEILING, MAX_ATTEMPTS, Watcher      # noqa: E402


class Reply:
    def __init__(self, stop="stop", text="a patch", out=10):
        self.stop_reason = stop
        self.text = text
        self.tokens_out = out


def test_a_finished_reply_is_left_alone():
    assert Watcher().observe("p3_implement", 3000, Reply()) is None


def test_a_truncated_reply_earns_a_larger_budget():
    """The answer was never reached; a bigger ask is the only thing that
    changes the outcome."""
    w = Watcher()
    nxt = w.observe("p3_implement", 3000, Reply(stop="length", text=""))
    assert nxt and nxt > 3000


@pytest.mark.parametrize("stop", ["length", "max_tokens", "MAX_OUTPUT_TOKENS"])
def test_every_spelling_of_truncation_is_recognised(stop):
    w = Watcher()
    assert w.observe("r", 1000, Reply(stop=stop, text="")) is not None


def test_an_empty_reply_that_spent_its_whole_budget_counts_as_truncated():
    """Some gateways report no stop reason at all. A reply that produced
    nothing while consuming the ceiling is the same event."""
    w = Watcher()
    w.granted["__last__"] = 3000
    assert w.observe("r", 3000, Reply(stop="", text="", out=2950)) is not None


def test_it_gives_up_rather_than_looping():
    """The rogue loop this exists to prevent: four cycles, 36k tokens, no
    patch, the same failure every time."""
    w = Watcher()
    asked, grants = 3000, []
    for _ in range(MAX_ATTEMPTS + 2):
        nxt = w.observe("p3_implement", asked, Reply(stop="length", text=""))
        grants.append(nxt)
        if nxt is None:
            break
        asked = nxt
    assert grants[-1] is None, "kept asking forever"
    assert len([g for g in grants if g]) <= MAX_ATTEMPTS


def test_the_budget_is_bounded():
    w = Watcher()
    asked = 20_000
    for _ in range(MAX_ATTEMPTS):
        nxt = w.observe("r", asked, Reply(stop="length", text=""))
        if nxt is None:
            break
        assert nxt <= CEILING
        asked = nxt


def test_roles_are_supervised_independently():
    """A judge running out of room says nothing about the implementer."""
    w = Watcher()
    for _ in range(MAX_ATTEMPTS):
        w.observe("p4_judge_diff", 300, Reply(stop="length", text=""))
    assert w.observe("p3_implement", 3000,
                     Reply(stop="length", text="")) is not None


def test_it_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("HARNESS_ADAPTIVE_TOKENS", "off")
    assert Watcher().observe("r", 3000, Reply(stop="length", text="")) is None


def test_a_retry_must_not_be_served_from_the_cache():
    """Re-serving a truncated reply cannot produce a different answer --
    the run showed `replayed` on every repeat."""
    import inspect

    from harness.model import gateway, router
    assert "no_cache" in inspect.signature(gateway.Gateway.call).parameters
    source = inspect.getsource(router.Router.call)
    assert "no_cache=True" in source


def test_the_router_retries_with_the_larger_budget():
    import inspect

    from harness.model.router import Router
    source = inspect.getsource(Router.call)
    assert "watcher.observe" in source
    assert "while budget" in source


# -- the size estimate ------------------------------------------------------
#
# A real run on mindmaze#7 spent all five cycles this way:
#
#   cycle 1  16 lines vs estimate 10 (cap 13)  -> rejected
#   cycle 2  21 lines                          -> rejected
#   cycle 3  21 lines                          -> rejected
#   cycle 4  21 lines  replayed                -> rejected
#   cycle 5  21 lines  replayed                -> rejected
#
# The model kept saying the fix needs ~21 lines. The estimate never moved,
# and the feedback said "re-scope with a tighter estimate" -- the opposite
# of what the evidence showed.

def test_one_rejection_is_not_evidence():
    from harness.watcher import SizeWatcher
    assert SizeWatcher().observe(16, 10) is None


def test_two_agreeing_attempts_raise_the_estimate():
    from harness.watcher import SizeWatcher
    w = SizeWatcher()
    w.observe(21, 10)
    assert w.observe(21, 10) == 21


def test_a_flailing_model_does_not_move_the_estimate():
    """Wildly different sizes mean the model is lost, not that the guess
    was too small."""
    from harness.watcher import SizeWatcher
    w = SizeWatcher()
    w.observe(12, 10)
    assert w.observe(90, 10) is None


def test_the_estimate_is_raised_once_and_bounded():
    from harness.watcher import SizeWatcher, SIZE_GROWTH
    w = SizeWatcher()
    w.observe(500, 10)
    first = w.observe(500, 10)
    assert first is not None and first <= int(10 * SIZE_GROWTH)
    assert w.observe(500, first) is None, "raised the estimate twice"


def test_a_genuinely_oversized_change_is_still_refused():
    from harness.watcher import SizeWatcher
    w = SizeWatcher()
    w.observe(400, 5)
    revised = w.observe(400, 5)
    assert revised is None or revised < 400


def test_the_orchestrator_reads_the_size_from_the_gate_message():
    from harness.orchestrator import _lines_in
    assert _lines_in("the change is 21 lines against an estimate of 10") == 21
    assert _lines_in("unrelated failure") == 0


def test_the_cycle_revises_rather_than_arguing():
    import inspect

    from harness.orchestrator import Orchestrator
    source = inspect.getsource(Orchestrator)
    assert "sizes.observe" in source
    assert 'exc.stage == "size"' in source
