"""Whisper mode on Photon's own call: every line is transcribed, nothing is said.

The property that matters above all is the second half. A whisper call is one
where the client was told the agent only listens; a single spoken word from
it — an answer, a greeting, a filler — breaks that.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json

import httpx

from adapters.room_listener import RoomListener
from orchestrator import Orchestrator


class _Adapter:
    def __init__(self):
        self.spoken = []

    async def speak(self, text, language=None):
        self.spoken.append(text)

    async def cancel_speech(self): pass
    async def announce(self, text): pass
    async def publish_event(self, event): pass


def _orch(listen_only):
    posted = []

    def handler(request):
        posted.append((request.url.path, json.loads(request.content or b"{}")))
        return httpx.Response(201, json={"ok": True})

    a = _Adapter()
    o = Orchestrator(adapter=a, brain_api_url="http://brain", meeting_slug="abcd-efgh",
                     listen_only=listen_only)
    o._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def no_brain(*args, **kwargs):
        raise AssertionError("whisper mode must never run a turn")
    if listen_only:
        o._ask_brain = no_brain
    return o, a, posted


def test_listen_only_records_every_speaker_and_says_nothing():
    o, a, posted = _orch(listen_only=True)

    async def run():
        await o.on_poke("guest:1", "Dana")
        await o.on_poke("user:42", "Priya")
        await o.on_speech("hello everyone", "guest:1", True)                  # greeting
        await o.on_speech("does the export support date filters?", "guest:1", True)
        await o.on_speech("good question, let me check", "user:42", True)
        await o.on_speech("half a sente", "guest:1", False)                   # interim
    asyncio.run(run())

    assert a.spoken == []
    lines = [(b["speaker_name"], b["text"]) for path, b in posted if path.endswith("/transcript")]
    assert lines == [("Dana", "hello everyone"),
                     ("Dana", "does the export support date filters?"),
                     ("Priya", "good question, let me check")]
    # The identity travels with the line: the server resolves member vs
    # client from it (a signed user:<id>), not from the display name.
    identities = [b["speaker_identity"] for path, b in posted if path.endswith("/transcript")]
    assert identities == ["guest:1", "guest:1", "user:42"]


def test_the_listener_itself_cannot_speak():
    listener = RoomListener(ctx=None, callbacks=None, stt_factory=None)

    async def run():
        await listener.speak("anything")
        await listener.announce("Hi, I'm Photon")
    asyncio.run(run())            # no-ops: nothing to assert but that nothing raised or played


def test_agents_and_screen_audio_are_not_transcribed():
    from livekit import rtc

    listener = RoomListener(ctx=None, callbacks=None, stt_factory=None)
    started = []
    listener._tasks = {}

    class P:
        def __init__(self, identity, kind=0):
            self.identity, self.kind, self.name = identity, kind, identity

    class Pub:
        def __init__(self, sid, source):
            self.sid, self.source = sid, source

    orig = asyncio.create_task
    asyncio.create_task = lambda coro: (started.append(coro), coro.close())[0]
    try:
        listener._listen(None, Pub("a", rtc.TrackSource.SOURCE_MICROPHONE), P("agent-AJ_x"))
        listener._listen(None, Pub("b", rtc.TrackSource.SOURCE_MICROPHONE),
                         P("x", rtc.ParticipantKind.PARTICIPANT_KIND_AGENT))
        listener._listen(None, Pub("c", rtc.TrackSource.SOURCE_SCREENSHARE_AUDIO), P("guest:1"))
        listener._listen(None, Pub("d", rtc.TrackSource.SOURCE_MICROPHONE), P("guest:1"))
        listener._listen(None, Pub("d", rtc.TrackSource.SOURCE_MICROPHONE), P("guest:1"))  # dup
    finally:
        asyncio.create_task = orig
    assert list(listener._tasks) == ["d"]
