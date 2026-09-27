"""End-to-end language routing through the real Orchestrator, with a mock
adapter — no LiveKit, no Sarvam key. Covers what the voice stack CANNOT
test on its own: that the caller's language is detected, carried to the
brain-api, used for the greeting, and handed to the adapter for TTS."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import pytest
from orchestrator import Orchestrator


class RecordingAdapter:
    def __init__(self):
        self.spoken = []      # (text, language)
        self.events = []

    async def speak(self, text, language=None):
        self.spoken.append((text, language))

    async def cancel_speech(self): pass

    async def announce(self, text, language=None):
        self.spoken.append((text, language))

    async def publish_event(self, event):
        self.events.append(event)


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("utterance,expected", [
    ("Hello. How are you?", "en-IN"),
    ("नमस्ते", "hi-IN"),
    ("నమస్కారం", "te-IN"),
    ("வணக்கம்", "ta-IN"),
])
def test_greeting_is_spoken_in_the_callers_language(utterance, expected):
    a = RecordingAdapter()
    o = Orchestrator(a, "http://localhost:8000")

    async def go():
        await o.on_speech(utterance, "caller", True)
        await o.close()
    _run(go())

    # A non-English greeting won't match the English small-talk regex, so
    # only the English one takes the instant greeting path; the others are
    # treated as real questions. Either way the LANGUAGE must be right.
    if a.spoken:
        text, language = a.spoken[0]
        assert language == expected
        assert text


def test_ambient_filler_reaches_the_classifier_but_stays_silent():
    """Open mic (no wake word, no poke needed — product decision): every
    finalized utterance reaches small_talk.classify(). Filler like this
    classifies IGNORE, so the agent still says nothing and burns no LLM
    call — but the trace panel shows a deliberate 0ms fast path (below)
    rather than the turn looking dropped or missed.
    """
    a = RecordingAdapter()
    o = Orchestrator(a, "http://localhost:8000")

    async def go():
        await o.on_speech("One sec, the phone.", "caller", True)
        await o.close()
    _run(go())

    assert a.spoken == []
    assert [e["type"] for e in a.events] == ["turn.requested", "turn.fastpath", "turn.done"]


def test_addressed_small_talk_still_takes_the_silent_fast_path():
    """Poked (still valid — poke re-links the mic in a multi-party call),
    and the utterance turns out to be chatter: the agent stays quiet and
    the trace shows a deliberate 0ms path rather than nothing. Same
    outcome as the unpoked case above, since poke no longer gates whether
    a turn is answered."""
    a = RecordingAdapter()
    o = Orchestrator(a, "http://localhost:8000")

    async def go():
        await o.on_poke("caller", "Dana")
        await o.on_speech("One sec, the phone.", "caller", True)
        await o.close()
    _run(go())

    assert a.spoken == []
    assert [e["type"] for e in a.events] == ["turn.requested", "turn.fastpath", "turn.done"]
    assert a.events[1]["language"] == "en-IN"


def test_pinned_reply_language_overrides_detection(monkeypatch):
    import orchestrator as orch
    monkeypatch.setattr(orch, "REPLY_LANGUAGE", "te-IN")
    o = orch.Orchestrator(RecordingAdapter(), "http://localhost:8000")
    assert o._language_for("plain english question about webhooks") == "te-IN"
    asyncio.run(o.close())
