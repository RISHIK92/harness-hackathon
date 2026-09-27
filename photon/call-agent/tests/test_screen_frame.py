"""Screen-frame buffering rules — a stale frame must never be sent."""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import pytest
from orchestrator import Orchestrator, SCREEN_FRAME_TTL_SECONDS


class _NullAdapter:
    async def speak(self, text, language=None): pass
    async def cancel_speech(self): pass
    async def announce(self, text, language=None): pass
    async def publish_event(self, event): pass


def _orch():
    return Orchestrator(_NullAdapter(), "http://localhost:8000")


def test_camera_frames_are_never_treated_as_screen():
    o = _orch()
    asyncio.run(o.on_frame(b"camera-bytes", "camera"))
    assert o.state.latest_screen_frame is None


def test_fresh_screen_frame_is_used():
    o = _orch()
    asyncio.run(o.on_frame(b"screen-bytes", "screen"))
    assert o._wants_visual_context("what's on my screen") is True


def test_stale_screen_frame_is_dropped_not_sent():
    o = _orch()
    asyncio.run(o.on_frame(b"screen-bytes", "screen"))
    # Customer stopped sharing a while ago: the pump ended, but the last
    # frame is still buffered.
    o.state.latest_screen_frame_at = time.time() - (SCREEN_FRAME_TTL_SECONDS + 5)
    assert o._wants_visual_context("what's on my screen") is False
    assert o.state.latest_screen_frame is None  # and it's cleared, not left to rot


def test_visual_question_without_any_frame_is_not_visual():
    o = _orch()
    assert o._wants_visual_context("what's on my screen") is False


# Regression: every one of these was logged on a REAL call, and every one
# was rejected by the old VISUAL_HINT_RE keyword gate while frames were
# actively flowing — so the agent answered "I don't have information" with
# the answer on screen in front of it. The English one missed because the
# pattern wanted `share` + a space and the caller said "shared"; the Telugu
# ones missed because the pattern was ASCII-only, which made the vision
# path and the multilingual path mutually exclusive.
LOGGED_MISSES = [
    "See, I have shared the screen right here. Help me write a message.",
    "స్క్రీన్ షేర్ చేశా కదా, ఇప్పుడు ఇక్కడ క్లార్డ్ తో ఎలా ఛార్జ్ చేయాలి?",
    "ఇక్కడ క్లాడ్ తో చాట్ ఎలా చేయాలి?",
]


@pytest.mark.parametrize("text", LOGGED_MISSES)
def test_real_call_utterances_attach_a_frame_while_sharing(text):
    o = _orch()
    asyncio.run(o.on_frame(b"screen-bytes", "screen"))
    assert o._wants_visual_context(text) is True


def test_any_question_attaches_a_frame_while_sharing():
    """An active share is the signal, not the wording.

    The inverse of the old behaviour and deliberately so: while someone is
    sharing, wording is a bad predictor of whether the screen is relevant,
    and guessing wrong is silent. Small talk never gets this far —
    _handle_turn returns early on any non-ANSWER intent — so the frame only
    ever rides along with a real question.
    """
    o = _orch()
    asyncio.run(o.on_frame(b"screen-bytes", "screen"))
    assert o._wants_visual_context("why does pricing have a special case for Bangalore?") is True


def test_nothing_attaches_once_the_share_stops():
    """The only thing that gates vision now is whether a share is LIVE."""
    o = _orch()
    asyncio.run(o.on_frame(b"screen-bytes", "screen"))
    o.state.latest_screen_frame_at = time.time() - (SCREEN_FRAME_TTL_SECONDS + 5)
    assert o._wants_visual_context("what's on my screen") is False
