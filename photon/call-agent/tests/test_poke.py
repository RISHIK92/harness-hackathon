"""Open mic: every finalized utterance is handled — no wake word or poke
required (explicit product decision; see orchestrator.py's on_speech
docstring). Poke still exists, but only for what it ALSO does: re-linking
which participant AgentSession listens to (adapters/livekit_adapter.py's
set_participant, exercised there, not here) and attaching a display name
for transcript lines, which IS orchestrator state and is tested below."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

from orchestrator import Orchestrator


class Adapter:
    def __init__(self):
        self.spoken = []

    async def speak(self, text, language=None):
        self.spoken.append(text)

    async def cancel_speech(self): pass
    async def announce(self, text, language=None): self.spoken.append(text)
    async def publish_event(self, event): pass


def _orch():
    o = Orchestrator(Adapter(), "http://localhost:8000")
    o.meeting_slug = None          # don't write transcripts from unit tests
    return o


def _capture_handle_turn(o):
    seen = []

    async def fake(question, history=None):
        seen.append(question)

    o._handle_turn = fake
    return seen


def test_speech_is_handled_without_any_poke():
    """No wake word, no poke — on_speech still reaches _handle_turn."""
    o = _orch()
    seen = _capture_handle_turn(o)

    asyncio.run(o.on_speech("why are Northwind's webhooks failing?", "user:alice", True))
    assert seen == ["why are Northwind's webhooks failing?"]


def test_a_second_speaker_is_handled_too_with_no_poke():
    """Multiple people talking, nobody poking — every one of them still
    gets answered, since the agent only ever hears whoever LiveKit has
    linked in (see set_participant); there's no separate in-orchestrator
    gate on top of that anymore."""
    o = _orch()
    seen = _capture_handle_turn(o)

    asyncio.run(o.on_speech("hang on, I'll pull up the dashboard", "user:bob", True))
    assert seen == ["hang on, I'll pull up the dashboard"]


def test_a_wake_word_prefix_is_still_stripped_if_present():
    """Saying the agent's name still works, out of habit — it's just not
    required. The prefix is stripped rather than sent to the brain verbatim."""
    o = _orch()
    seen = _capture_handle_turn(o)

    asyncio.run(o.on_speech("Photon, what does a 401 mean here?", "guest:client", True))
    assert seen == ["what does a 401 mean here?"]


def test_poke_records_a_display_name_for_the_transcript():
    """The mic re-link itself happens in the adapter, before this callback
    fires — on_poke's own job now is just remembering who's who."""
    o = _orch()
    asyncio.run(o.on_poke("guest:client-a", "Client A"))
    assert o.state.names["guest:client-a"] == "Client A"


def test_non_final_or_empty_speech_is_still_ignored():
    o = _orch()
    seen = _capture_handle_turn(o)

    asyncio.run(o.on_speech("still talking", "user:alice", False))
    asyncio.run(o.on_speech("   ", "user:alice", True))
    assert seen == []
