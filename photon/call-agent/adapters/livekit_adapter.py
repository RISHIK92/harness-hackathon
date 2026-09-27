"""The one TransportAdapter implementation we ship. Wraps a LiveKit Agents
1.x AgentSession: joins the room, wires Deepgram STT/TTS + Silero VAD,
intercepts every user turn to hand off to the orchestrator instead of
letting LiveKit's own LLM node run, and separately watches for a
screen-share video track (source == SCREEN_SHARE, never the camera track)
to feed on_frame at a low, state-dependent frame rate.

Everything platform-specific lives in this one file. adapters/base.py is
the only thing orchestrator.py depends on.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import time

import structlog
from livekit import agents, rtc
from livekit.agents import StopResponse
from livekit.plugins import deepgram, silero

from adapters.base import SessionCallbacks

log = structlog.get_logger()

# Which STT/TTS vendor to use. Deepgram stays the default so nothing about
# an existing setup changes; VOICE_STACK=sarvam swaps in Sarvam's Indic
# models (bulbul TTS / saaras STT), which is the only way this agent can
# speak Telugu, Tamil or Hindi at all — deepgram's aura-2-thalia-en is
# English-only.
# Deployment-wide fallback. The MEETING's own language_mode overrides it
# when the worker can fetch the call config — the stack is a per-call
# decision (an English-only support call wants Deepgram's lower latency; a
# call with Telugu speakers needs Sarvam), not a property of the server.
VOICE_STACK = os.environ.get("VOICE_STACK", "deepgram").strip().lower()
SARVAM_TTS_MODEL = os.environ.get("SARVAM_TTS_MODEL", "bulbul:v3")
SARVAM_TTS_SPEAKER = os.environ.get("SARVAM_TTS_SPEAKER") or None
# saaras:v3 with language="unknown" auto-detects the spoken language, which
# is what makes one agent handle four of them without being told first.
SARVAM_STT_MODEL = os.environ.get("SARVAM_STT_MODEL", "saaras:v3")
SARVAM_STT_LANGUAGE = os.environ.get("SARVAM_STT_LANGUAGE", "unknown")

# What the agent says on joining. The company name is filled in from the
# call's own workspace when there is one; it used to name the fictional demo
# company on every real call.
ANNOUNCEMENT_TEMPLATE = (
    "Hi, I'm {agent}, {org}'s support agent. I'm listening and taking notes — "
    "let me know if you'd like me off."
)
ANNOUNCEMENT_GENERIC = (
    "Hi, I'm {agent}. I'm listening and taking notes — "
    "let me know if you'd like me off."
)


# Said when the agent attends FOR a member: it names itself as their agent,
# never as them, and says up front that it will check with them — so the
# holding line later ("let me check with Priya") is expected, not a surprise.
ANNOUNCEMENT_FOR_MEMBER = (
    "Hi, I'm {agent}, attending for {poc} today. I'll answer what I can and "
    "check with {poc} on anything I can't — let me know if you'd like me off."
)


def announcement_for(org_name: str | None, agent_name: str | None = None,
                     poc_name: str | None = None) -> str:
    org = (org_name or "").strip()
    agent = (agent_name or "").strip() or "Photon"
    if (poc_name or "").strip():
        return ANNOUNCEMENT_FOR_MEMBER.format(agent=agent, poc=poc_name.strip())
    return (
        ANNOUNCEMENT_TEMPLATE.format(org=org, agent=agent)
        if org
        else ANNOUNCEMENT_GENERIC.format(agent=agent)
    )

# Topic the browser filters on (client/app/call/TraceBridge.tsx) so trace
# events never get confused with chat or any other data traffic.
TRACE_TOPIC = "photon.trace"

# Participants poke the agent to address it. Without this the session
# listens to whoever LiveKit happened to link first and answers every
# utterance from them — unworkable once a call has several humans and
# external guests on it.
POKE_TOPIC = "photon.poke"

_SCREEN_FRAME_INTERVAL_SPEAKING = 1.0  # ~1 fps while someone is speaking
_SCREEN_FRAME_INTERVAL_IDLE = 1.0 / 0.3  # ~0.3 fps otherwise
_SCREEN_FRAME_MAX_DIM = 1024


# Endpointing decides when a caller has finished speaking. The two delays
# apply to DIFFERENT cases, which is what makes raising the max cheap:
# when the turn detector is confident the sentence ended, the turn commits
# after `min_delay`; `max_delay` only applies when the detector thinks the
# caller is NOT finished and is waiting for them to continue.
#
# The stock 2.5s max truncated a real question on a live call. The caller
# said "Oh, why do you", paused to think, and the turn committed
# mid-sentence with an end-of-turn probability of 0.096 against a 0.56
# threshold — the detector KNEW they were not done and cut them off
# anyway. The agent then answered the fragment, which to the person on the
# call is indistinguishable from the agent not listening.
#
# So the max is raised to give a hesitating speaker room to finish. It
# costs nothing on fluent speech (that commits at `min_delay`) and only
# spends time in the case where the alternative is answering half a
# question. The min is lowered slightly because a confidently-finished
# sentence should not wait at all on a live call.
_ENDPOINTING = {"min_delay": 0.25, "max_delay": 6.0}


def build_stt(voice_stack: str | None = None):
    """The call's STT, shared by speak mode (one AgentSession) and whisper
    mode (adapters/room_listener.py, one stream per participant)."""
    if (voice_stack or VOICE_STACK).strip().lower() == "sarvam":
        from livekit.plugins import sarvam

        log.info("livekit_adapter.stt", vendor="sarvam", model=SARVAM_STT_MODEL,
                 language=SARVAM_STT_LANGUAGE)
        return sarvam.STT(model=SARVAM_STT_MODEL, language=SARVAM_STT_LANGUAGE, mode="transcribe")
    log.info("livekit_adapter.stt", vendor="deepgram", model="nova-3")
    return deepgram.STT(model="nova-3")


class _InterceptAgent(agents.Agent):
    """Every turn is handed to the orchestrator via on_speech; StopResponse
    always prevents LiveKit's own (unconfigured) LLM node from also trying
    to generate a reply — this agent never composes an answer itself."""

    def __init__(self, callbacks: SessionCallbacks, speaker_id_fn):
        super().__init__(instructions="unused — this agent never generates its own replies")
        self._callbacks = callbacks
        # A callable, not a fixed string: who is speaking changes during a
        # call, and it was previously captured ONCE at construction — as
        # the agent's own identity, so every human utterance was attributed
        # to the agent. Visible in the logs as
        # `speech_finalized speaker_id=agent-AJ_...` for things a person
        # said. Everything that depends on identity (transcripts,
        # per-speaker source scoping) was wrong because of it.
        self._speaker_id_fn = speaker_id_fn

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        text = (new_message.text_content or "").strip()
        if text:
            await self._callbacks.on_speech(text, self._speaker_id_fn(), is_final=True)
        raise StopResponse()


class LiveKitAdapter:
    """Implements TransportAdapter (speak/cancel_speech/announce)."""

    def __init__(
        self,
        ctx: agents.JobContext,
        callbacks: SessionCallbacks,
        voice_stack: str | None = None,
        org_name: str | None = None,
        agent_name: str | None = None,
        poc_name: str | None = None,
    ):
        self._ctx = ctx
        self._callbacks = callbacks
        self._session: agents.AgentSession | None = None
        self._screen_task: asyncio.Task | None = None
        self._tts = None
        self._tts_language: str | None = None
        self._voice_stack = (voice_stack or VOICE_STACK).strip().lower()
        self._org_name = org_name
        self._agent_name = agent_name
        self._poc_name = poc_name

    def _build_stt(self):
        return build_stt(self._voice_stack)

    def _build_tts(self):
        if self._voice_stack == "sarvam":
            from livekit.plugins import sarvam

            log.info("livekit_adapter.tts", vendor="sarvam", model=SARVAM_TTS_MODEL,
                     speaker=SARVAM_TTS_SPEAKER)
            kwargs = {"model": SARVAM_TTS_MODEL, "target_language_code": "en-IN"}
            if SARVAM_TTS_SPEAKER:
                kwargs["speaker"] = SARVAM_TTS_SPEAKER
            return sarvam.TTS(**kwargs)
        log.info("livekit_adapter.tts", vendor="deepgram", model="aura-2-thalia-en")
        return deepgram.TTS(model="aura-2-thalia-en")

    async def start(self) -> None:
        await self._ctx.connect()

        self._tts = self._build_tts()
        self._session = agents.AgentSession(
            stt=self._build_stt(),
            tts=self._tts,
            vad=silero.VAD.load(),
            # Endpointing decides when a caller has finished speaking.
            #
            # The two delays apply to DIFFERENT cases, which is what makes
            # this cheap. When the turn detector is confident the sentence
            # ended, the turn commits after `min_delay`. `max_delay` only
            # applies when the detector thinks the caller is NOT finished
            # and is waiting for them to continue.
            #
            # The stock 2.5s max truncated a real question live: the caller
            # said "Oh, why do you", paused to think, and the turn committed
            # mid-sentence with an end-of-turn probability of 0.096 against
            # a 0.56 threshold — the model KNEW they were not done and cut
            # them off anyway. The agent then answered the fragment, which
            # reads as the agent not listening.
            #
            # So `max_delay` is raised to give a hesitating speaker room to
            # finish. It costs nothing on fluent speech (that path commits
            # at `min_delay`) and only spends time in the case where the
            # alternative is answering half a question. `min_delay` is
            # lowered a little because a confidently-finished sentence
            # should not wait at all on a live call.
            turn_handling={"endpointing": _ENDPOINTING},
        )
        # Logged because a wrong endpointing value is invisible otherwise:
        # it shows up as the agent answering half a question, which reads
        # as a model problem rather than a timing one.
        log.info("livekit_adapter.endpointing", **_ENDPOINTING)
        agent = _InterceptAgent(self._callbacks, speaker_id_fn=self._current_speaker_id)

        self._ctx.room.on("track_subscribed", self._on_track_subscribed)
        self._ctx.room.on("data_received", self._on_data_received)
        # `track_subscribed` only fires for tracks that arrive AFTER this
        # handler is attached — and ctx.connect() above has already run, so
        # a participant who was ALREADY sharing their screen when the agent
        # joined would never produce a single frame. That is the common
        # case whenever the worker restarts or the agent is re-dispatched
        # into a call that is already in progress, so sweep for existing
        # screen-share tracks explicitly.
        self._attach_existing_screenshare()

        await self._session.start(
            agent=agent,
            room=self._ctx.room,
            room_input_options=agents.RoomInputOptions(video_enabled=True),
        )
        await self.announce(announcement_for(self._org_name, self._agent_name, self._poc_name))

    def _current_speaker_id(self) -> str:
        """Identity of the participant the session is actually listening to.

        LiveKit's RoomIO links exactly ONE participant at a time
        (`linked_participant` / `set_participant`), so this is unambiguous
        — and it is the human, not us. Falls back to the local identity
        only if nothing is linked yet, which should not happen for a real
        turn.
        """
        room_io = getattr(self._session, "_room_io", None) or getattr(self._session, "room_io", None)
        linked = getattr(room_io, "linked_participant", None) if room_io else None
        if linked is not None and getattr(linked, "identity", None):
            return linked.identity
        log.warning("livekit_adapter.speaker_unresolved")
        return self._ctx.room.local_participant.identity or "unknown"

    def _on_data_received(self, packet: rtc.DataPacket) -> None:
        """A participant pressed "Ask Photon".

        LiveKit's AgentSession listens to exactly ONE participant at a time
        (RoomIO.linked_participant), so a poke is not just an intent
        signal — it is how we choose whose microphone the agent is on. It
        also makes attribution certain, which matters because the answer
        may draw on that person's own private sources.

        The identity comes from the packet's sender, not from its payload:
        LiveKit tells us who actually sent it, so nobody can poke as
        somebody else.
        """
        if packet.topic != POKE_TOPIC:
            return
        participant = packet.participant
        if participant is None:
            log.warning("livekit_adapter.poke_without_sender")
            return
        asyncio.create_task(self._link_speaker(participant))

    async def _link_speaker(self, participant: rtc.RemoteParticipant) -> None:
        room_io = getattr(self._session, "_room_io", None) or getattr(self._session, "room_io", None)
        if room_io is None:
            log.warning("livekit_adapter.poke_no_room_io")
            return
        try:
            current = getattr(room_io.linked_participant, "identity", None)
            if current != participant.identity:
                room_io.set_participant(participant.identity)
            log.info(
                "livekit_adapter.poked",
                identity=participant.identity,
                name=participant.name,
                previous=current,
            )
            await self._callbacks.on_poke(participant.identity, participant.name or participant.identity)
        except Exception as exc:  # noqa: BLE001 - a bad poke must not end the call
            log.error("livekit_adapter.poke_failed", error=str(exc))

    def _attach_existing_screenshare(self) -> None:
        for participant in self._ctx.room.remote_participants.values():
            for publication in participant.track_publications.values():
                if (
                    publication.source == rtc.TrackSource.SOURCE_SCREENSHARE
                    and publication.subscribed
                    and publication.track is not None
                ):
                    log.info(
                        "livekit_adapter.screen_share_already_active",
                        participant=participant.identity,
                    )
                    self._start_screen_pump(publication.track)

    def _start_screen_pump(self, track: rtc.Track) -> None:
        # Replacing a live task would leak the old one; stop it first. This
        # happens when a customer stops and restarts sharing mid-call.
        if self._screen_task and not self._screen_task.done():
            self._screen_task.cancel()
        self._screen_task = asyncio.create_task(self._pump_screen_frames(track))

    def _on_track_subscribed(
        self,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        if track.kind != rtc.TrackKind.KIND_VIDEO:
            return
        if publication.source != rtc.TrackSource.SOURCE_SCREENSHARE:
            return  # never feed the camera track in here — see module docstring
        log.info("livekit_adapter.screen_share_subscribed", participant=participant.identity)
        self._start_screen_pump(track)

    async def _pump_screen_frames(self, track: rtc.Track) -> None:
        try:
            from PIL import Image
        except ImportError:
            log.warning("livekit_adapter.pillow_missing_skipping_frames")
            return

        # NOTE the keyword: from_track() is keyword-ONLY in livekit rtc.
        # Passing the track positionally raises TypeError — and because
        # this used to sit outside the try/except below, inside a task
        # nobody awaits, that exception vanished completely: the
        # screen_share_subscribed log line appeared, zero frames arrived,
        # and no error was ever printed. Caught only by testing the real
        # signature. Everything from here down is inside the try for that
        # reason: a pump that dies must say so.
        last_sent = 0.0
        stream = None
        try:
            stream = rtc.VideoStream.from_track(track=track, format=rtc.VideoBufferType.RGBA)
            log.info("livekit_adapter.screen_pump_started")
            frames_seen = 0
            async for event in stream:
                frame = event.frame
                interval = (
                    _SCREEN_FRAME_INTERVAL_SPEAKING
                    if self._session and self._session.user_state == "speaking"
                    else _SCREEN_FRAME_INTERVAL_IDLE
                )
                now = time.monotonic()
                if now - last_sent < interval:
                    continue
                last_sent = now

                img = Image.frombytes("RGBA", (frame.width, frame.height), bytes(frame.data)).convert("RGB")
                img.thumbnail((_SCREEN_FRAME_MAX_DIM, _SCREEN_FRAME_MAX_DIM))
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=70)
                await self._callbacks.on_frame(buf.getvalue(), "screen")
                frames_seen += 1
                if frames_seen == 1 or frames_seen % 30 == 0:
                    # Silence used to be indistinguishable from "working
                    # fine, nobody asked a visual question yet".
                    log.info("livekit_adapter.screen_frames_flowing", frames=frames_seen,
                             size_bytes=len(buf.getvalue()))
        except Exception as exc:  # noqa: BLE001 - a frame-pump failure must not take the call down
            log.error("livekit_adapter.screen_pump_error", error=type(exc).__name__ + ": " + str(exc))
        finally:
            if stream is not None:
                await stream.aclose()

    # ── TransportAdapter ─────────────────────────────────────────────────

    async def speak(self, text: str, language: str | None = None) -> None:
        if not self._session:
            log.warning("livekit_adapter.speak_before_start", text=text)
            return
        self._retarget_tts(language)
        self._session.say(text)

    def _retarget_tts(self, language: str | None) -> None:
        """Point the TTS at the caller's language before speaking.

        Sarvam's plugin exposes update_options(), so one AgentSession can
        switch language per turn — no need to tear down and rebuild the
        session when a caller switches from Hindi to English mid-call.
        Deepgram's aura-2-thalia-en has no such knob (it is English-only),
        so this is a no-op there rather than an error: the answer still
        gets spoken, just in an English voice.
        """
        if not language or language == self._tts_language or self._voice_stack != "sarvam":
            return
        try:
            self._tts.update_options(target_language_code=language)
            self._tts_language = language
            log.info("livekit_adapter.tts_language_switched", language=language)
        except Exception as exc:  # noqa: BLE001 - never lose the answer over a voice setting
            log.warning("livekit_adapter.tts_language_switch_failed", language=language, error=str(exc))

    async def cancel_speech(self) -> None:
        if not self._session:
            return
        self._session.interrupt()

    async def announce(self, text: str, language: str | None = None) -> None:
        await self.speak(text, language)

    async def publish_event(self, event: dict) -> None:
        """Broadcast one trace event to every participant over LiveKit's
        data channel. Reliable, since a dropped tool.done would leave the
        panel showing a tool as still running forever."""
        try:
            await self._ctx.room.local_participant.publish_data(
                json.dumps(event), reliable=True, topic=TRACE_TOPIC
            )
        except Exception as exc:  # noqa: BLE001 - a UI event must never take the call down
            log.warning("livekit_adapter.publish_event_failed", type=event.get("type"), error=str(exc))
