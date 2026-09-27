"""Whisper mode on Photon's own call: hear EVERYONE, say nothing.

Speak mode runs one AgentSession, and an AgentSession listens to exactly one
linked participant at a time — fine for answering whoever poked it, useless
for whisper, whose whole job is to catch the client's question no matter who
is holding the floor. So whisper mode does not use AgentSession at all: every
remote participant's microphone gets its own STT stream, and every final
transcript is handed to the orchestrator attributed to the participant it
came from.

It implements TransportAdapter so the orchestrator needs no whisper branch
for output — but speak/announce are no-ops by design. The agent is in the
room only as a listener; nothing it knows is ever said aloud to the client.

Cost, stated plainly: one STT stream per speaking participant, running for
the whole call whether or not anyone asks anything. That is the price of a
full-room transcript and the reason whisper is a per-call choice.
"""
from __future__ import annotations

import asyncio

import structlog
from livekit import agents, rtc
from livekit.agents import stt as agents_stt

from adapters.base import SessionCallbacks

log = structlog.get_logger()

# What a participant's audio is resampled to before STT. Deepgram's default;
# RecognizeStream resamples anyway, this just keeps the pushed rate stable.
_SAMPLE_RATE = 16000


class RoomListener:
    def __init__(self, ctx: agents.JobContext, callbacks: SessionCallbacks, stt_factory):
        self._ctx = ctx
        self._callbacks = callbacks
        self._stt_factory = stt_factory
        self._stt = None
        self._vad = None
        # One task per (participant, track). Keyed by track sid so a
        # re-published microphone replaces its stream instead of doubling it.
        self._tasks: dict[str, asyncio.Task] = {}

    # ── TransportAdapter: silent by design ───────────────────────────────

    async def speak(self, text: str, language: str | None = None) -> None:
        log.debug("room_listener.speak_suppressed", chars=len(text or ""))

    async def cancel_speech(self) -> None:
        return None

    async def announce(self, text: str, language: str | None = None) -> None:
        return None

    async def publish_event(self, event: dict) -> None:
        return None

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        await self._ctx.connect(auto_subscribe=agents.AutoSubscribe.AUDIO_ONLY)
        stt = self._stt_factory()
        if not stt.capabilities.streaming:
            # Sarvam's saaras is request/response; a VAD cuts the stream into
            # utterances so it can be used like a streaming recogniser.
            from livekit.plugins import silero

            self._vad = self._vad or silero.VAD.load()
            stt = agents_stt.StreamAdapter(stt=stt, vad=self._vad)
        self._stt = stt

        room = self._ctx.room
        room.on("track_subscribed", self._on_track_subscribed)
        room.on("track_unsubscribed", self._on_track_unsubscribed)
        room.on("participant_disconnected", self._on_participant_disconnected)
        # Participants already talking when the agent joined: track_subscribed
        # has fired for them before the handler existed.
        for participant in room.remote_participants.values():
            for publication in participant.track_publications.values():
                if publication.track is not None and publication.kind == rtc.TrackKind.KIND_AUDIO:
                    self._listen(publication.track, publication, participant)
        log.info("room_listener.started", room=room.name,
                 participants=len(room.remote_participants))

    async def close(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        self._tasks.clear()

    # ── tracks ───────────────────────────────────────────────────────────

    @staticmethod
    def _is_agent(participant: rtc.RemoteParticipant) -> bool:
        return (getattr(participant, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
                or (participant.identity or "").startswith("agent-"))

    def _on_track_subscribed(self, track: rtc.Track, publication: rtc.RemoteTrackPublication,
                             participant: rtc.RemoteParticipant) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            self._listen(track, publication, participant)

    def _on_track_unsubscribed(self, track: rtc.Track, publication: rtc.RemoteTrackPublication,
                               participant: rtc.RemoteParticipant) -> None:
        task = self._tasks.pop(publication.sid, None)
        if task:
            task.cancel()

    def _on_participant_disconnected(self, participant: rtc.RemoteParticipant) -> None:
        for sid in [s for s in participant.track_publications]:
            task = self._tasks.pop(sid, None)
            if task:
                task.cancel()

    def _listen(self, track: rtc.Track, publication, participant: rtc.RemoteParticipant) -> None:
        # Screen-share audio is a video's soundtrack, not a person talking;
        # another agent is not a participant whose questions we answer.
        if publication.source == rtc.TrackSource.SOURCE_SCREENSHARE_AUDIO or self._is_agent(participant):
            return
        if publication.sid in self._tasks:
            return
        name = participant.name or participant.identity
        self._tasks[publication.sid] = asyncio.create_task(
            self._transcribe(track, participant.identity, name)
        )

    async def _transcribe(self, track: rtc.Track, identity: str, name: str) -> None:
        # Records the display name so transcript lines say "Dana", not a raw
        # LiveKit identity. on_poke's only effect in the orchestrator is
        # exactly that bookkeeping; nobody poked anyone here.
        await self._callbacks.on_poke(identity, name)
        audio = rtc.AudioStream.from_track(track=track, sample_rate=_SAMPLE_RATE, num_channels=1)
        stream = self._stt.stream()
        log.info("room_listener.listening", identity=identity)

        async def pump() -> None:
            async for event in audio:
                stream.push_frame(event.frame)
            stream.end_input()

        pusher = asyncio.create_task(pump())
        try:
            async for event in stream:
                if event.type != agents_stt.SpeechEventType.FINAL_TRANSCRIPT or not event.alternatives:
                    continue
                text = (event.alternatives[0].text or "").strip()
                if text:
                    await self._callbacks.on_speech(text, identity, True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — one participant's STT failing must not end the call
            log.warning("room_listener.stt_failed", identity=identity, error=str(exc))
        finally:
            pusher.cancel()
            await stream.aclose()
            await audio.aclose()
            log.info("room_listener.stopped", identity=identity)
