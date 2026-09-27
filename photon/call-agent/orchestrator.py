"""Platform-blind session orchestrator: turn state, rolling transcript,
latest screen frame, and the HTTP call out to the Company Brain
(server/app/agent, via POST /api/agent/ask). Implements SessionCallbacks
(adapters/base.py) — a concrete adapter calls into this; this module never
imports a concrete adapter, only the base contract.

Open mic, not explicit address: every finalized user turn is considered
(per explicit user instruction — the build plan's original Phase 4 design
used a "Photon" wake word instead; that's been dropped here). What used
to be the wake-word gate is now `small_talk.classify()`: a local, regex-
only triage that answers greetings instantly and stays silent on ambient
chatter, so only real requests pay for the pipeline. Measured live before
this existed: "Hello. How are you?" cost 4.5s and a needless search_docs
call to answer a greeting.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from dataclasses import dataclass, field

import httpx
import structlog

from adapters.base import TransportAdapter
from language import (
    DEFAULT_LANGUAGE,
    acknowledgement_for,
    detect_language,
    greeting_for,
    lookup_filler_for,
)
from small_talk import Turn, classify
from speech import for_speech, with_thinking_sound

log = structlog.get_logger()

# Broadened after the original pattern missed a real utterance ("check my
# screen and help me open the search bar?") — "check/look at my screen",
# "share my screen", "help me find/open/see", "what's on (my) screen" all
# now match, not just the original narrow "where do i"/"this screen" set.
# Saying its name is the fallback for anyone not using our join page (a
# phone/SIP caller, another client). Note the real limitation: LiveKit's
# session listens to one linked participant at a time, so a wake word is
# only heard from whoever is currently linked — the button is what makes
# addressing work for everyone else.
def wake_word_re(agent_name: str) -> re.Pattern:
    """The wake word IS the agent's name, so it has to be built per call.

    A workspace that renamed its agent to Ava has people saying "Ava, …" —
    listening for "Photon" would mean the wake word simply never fires for
    them, silently, and only the button would work.
    """
    return re.compile(rf"^\s*(hey\s+|ok\s+)?{re.escape(agent_name)}\b[\s,:-]*", re.IGNORECASE)


DEFAULT_AGENT_NAME = "Photon"

# A screen frame is only meaningful while the customer is actually
# sharing. Frames arrive at ~0.3-1fps during a share, so anything older
# than this means the share stopped (or dropped) and the buffered frame no
# longer shows what's on their screen. Without this the last frame lives
# forever: the customer stops sharing, asks "what's on my screen?" twenty
# minutes later, and the agent confidently describes a screen that hasn't
# existed for twenty minutes — real bytes, dead reality.
SCREEN_FRAME_TTL_SECONDS = 30.0

# "auto" detects the caller's language per utterance from the transcript's
# script (language.py). Pin it to a BCP-47 code (te-IN, ta-IN, hi-IN,
# en-IN) to force every reply into one language — useful when the STT in
# use romanises Indic speech, which defeats script detection.
REPLY_LANGUAGE = os.environ.get("AGENT_REPLY_LANGUAGE", "auto").strip()

# How many earlier turns ride along with each question. Six is three
# exchanges, which covers the follow-up chains that actually happen on a
# call ("why is that?" -> "and for the other one?") without turning every
# turn's prompt into a transcript — the history block is input tokens on
# the critical path, and an old turn is a weaker referent than a recent one.
HISTORY_TURNS = 6

# How long the caller may sit in silence before the agent says something.
#
# A turn is ~2.1-2.9s of plan + tools + compose, and all of it is silent. On a
# call that reads as the agent having missed the question, so people repeat
# themselves and then talk over the answer. Every voice-agent guide says the
# same thing in different words — LiveKit's own example puts it as "before a
# tool call that takes a moment, give a brief spoken acknowledgment first so
# there's no dead air".
#
# 1.5s. A normal turn is ~2.5-3.5s, so this fires on most of them — which is
# the intent: the silence is what reads as the agent having missed the
# question. Raise it toward 2.5s if that proves chatty on a real call, or set
# AGENT_ACK_AFTER_SECONDS=0 to switch it off.
#
# It does NOT make the answer later: the filler is spoken while the brain-api
# call is still in flight, not before it starts.
ACK_AFTER_SECONDS = float(os.environ.get("AGENT_ACK_AFTER_SECONDS", "1.5"))

# Once "checking, one moment" has been said, hold the answer briefly rather
# than letting it tread on the heels of the filler. LiveKit queues speech, so
# the two can never overlap — but an answer enqueued in the same instant reads
# as one run-on sentence ("checking one moment it runs on Postgres"), which is
# worse than the silence the filler was there to fill. If the filler is still
# playing this costs nothing at all, since the answer was going to wait anyway.
POST_FILLER_PAUSE_SECONDS = float(os.environ.get("AGENT_POST_FILLER_PAUSE", "1.0"))

# Escalation: when the agent cannot stand behind an answer and the question
# matters, it asks its person (the member it attends for) instead of saying
# "I don't know". The brain-api decides whether, and owns the clock; this
# only polls. The poll interval is the latency between the member hitting
# send and the answer being spoken, so it is short.
ESCALATION_POLL_SECONDS = float(os.environ.get("AGENT_ESCALATION_POLL", "2.0"))


@dataclass
class TurnState:
    # Written since Phase 4 and, until conversation memory existed, never
    # read — which is exactly why a follow-up had no referent. `history()`
    # below is now the only reader, and the brain-api the only consumer.
    transcript: list[dict] = field(default_factory=list)
    latest_screen_frame: bytes | None = None
    latest_screen_frame_at: float = 0.0
    # Filled in by on_poke (a poke packet carries the sender's display
    # name), so a transcript line can say "Priya" instead of a raw LiveKit
    # identity. Not used for gating anymore — open mic answers whoever is
    # linked regardless of whether they've ever poked.
    names: dict = field(default_factory=dict)
    # Counts visual turns only, so the spoken acknowledgement rotates
    # through its variants rather than repeating one line every time
    # somebody asks about their screen.
    visual_turns: int = 0
    # Rotates the no-dead-air filler, for the same reason visual_turns
    # rotates the visual one: hearing the identical phrase every slow turn is
    # worse than the silence it replaces.
    filler_turns: int = 0
    # Rotates the thinking sound ("Hmm,", "Ah,", …) so it neither repeats nor
    # lands on every single turn.
    spoken_turns: int = 0
    # When the last "checking, one moment" was spoken, so the answer can be
    # held relative to IT rather than for a flat delay after it arrives.
    filler_spoken_at: float = 0.0

    def history(self, limit: int = HISTORY_TURNS) -> list[dict]:
        """The conversation so far, oldest last, in the brain-api's shape.

        Every speaker's lines, not just the one now asking: on a multi-party
        call the referent of "why is that?" is frequently something a
        COLLEAGUE said, and a per-speaker history would drop precisely the
        line being referred to.

        The agent's own replies are included, and they matter more than the
        questions do — "is that documented?" refers to what was ANSWERED.
        """
        return [{"role": t["role"], "text": t["text"]} for t in self.transcript[-limit:]]


class Orchestrator:
    """Implements SessionCallbacks. Holds no transport objects of its own —
    just a TransportAdapter reference to act through."""

    def __init__(
        self,
        adapter: TransportAdapter,
        brain_api_url: str,
        meeting_slug: str | None = None,
        agent_name: str | None = None,
        listen_only: bool = False,
    ):
        self.adapter = adapter
        self.brain_api_url = brain_api_url.rstrip("/")
        # The LiveKit room name IS the meeting slug (abcd-efgh), so the
        # room, the share link and the transcript are one identifier.
        self.meeting_slug = meeting_slug
        self.agent_name = (agent_name or "").strip() or DEFAULT_AGENT_NAME
        self._wake_word = wake_word_re(self.agent_name)
        self.state = TurnState()
        self._http = httpx.AsyncClient(timeout=90.0)
        # Whisper mode: every line goes to the transcript (which feeds the
        # meeting's whisper session server-side) and NOTHING is answered
        # aloud — suggestions land privately in each member's thread.
        self.listen_only = listen_only
        # Open escalations being waited on. Held so close() can cancel them:
        # a poll that outlives the call would speak into a room nobody is in.
        self._escalations: set[asyncio.Task] = set()
        self._escalation_turns = 0

    async def close(self) -> None:
        for task in list(self._escalations):
            task.cancel()
        await self._http.aclose()

    # ── SessionCallbacks ──────────────────────────────────────────────────

    async def on_poke(self, speaker_id: str, display_name: str) -> None:
        # The actual mic re-link already happened in the adapter
        # (set_participant, before this callback fires) — this just
        # records the display name for transcript lines.
        self.state.names[speaker_id] = display_name
        log.info("orchestrator.poked", speaker_id=speaker_id, name=display_name)

    async def on_speech(self, text: str, speaker_id: str, is_final: bool) -> None:
        """Open mic: every finalized utterance from the currently linked
        participant is answered — no wake word or poke required, per
        explicit product request. Poke (`on_poke` above) and the wake word
        still matter for what they ALSO do: `on_poke` re-links which
        participant `AgentSession` listens to on a multi-party call (see
        livekit_adapter.py's `set_participant`) — a separate question from
        whether to answer, which is now always yes for whoever is linked.
        """
        if not is_final or not text.strip():
            return

        speaker_name = self.state.names.get(speaker_id, speaker_id)
        # Read BEFORE this utterance is appended: history means the turns
        # BEFORE this one. Included, it would hand the model the question it
        # is already being asked, as though it were its own antecedent.
        history = self.state.history()
        self.state.transcript.append(
            {"role": "user", "speaker_id": speaker_id, "text": text, "ts": time.time()}
        )
        log.info("orchestrator.speech_finalized", speaker_id=speaker_id, text=text)

        await self._record_transcript("human", speaker_name, text, speaker_id)
        if self.listen_only:
            return

        await self._handle_turn(
            self._wake_word.sub("", text.strip(), count=1).strip() or text, history=history
        )

    async def _record_transcript(
        self, role: str, speaker_name: str, text: str, speaker_identity: str | None = None
    ) -> None:
        """Best-effort: a transcript write must never delay or break a call."""
        if not self.meeting_slug:
            return
        try:
            await self._http.post(
                f"{self.brain_api_url}/api/meetings/{self.meeting_slug}/transcript",
                json={
                    "role": role,
                    "speaker_name": speaker_name,
                    "speaker_identity": speaker_identity,
                    "text": text,
                },
                timeout=10.0,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("orchestrator.transcript_write_failed", error=str(exc))

    async def on_frame(self, image: bytes, source: str) -> None:
        if source != "screen":
            return
        self.state.latest_screen_frame = image
        self.state.latest_screen_frame_at = time.time()

    # ── internals ─────────────────────────────────────────────────────────

    def _wants_visual_context(self, question: str) -> bool:
        """An ACTIVE screen share is the signal — not the wording of the question.

        This used to also require VISUAL_HINT_RE to match, and that keyword
        gate silently ate every real screen-share turn we have logged:

          - "See, I have shared the screen right here" missed, because the
            pattern wants `share` followed by a space and the caller said
            "shared";
          - every Indic-language turn missed unconditionally, because the
            pattern is ASCII-only — so the vision path and the multilingual
            path were mutually exclusive, which nobody intended.

        Both are the same class of bug: a keyword list is a PROXY for "is
        this about the screen", and a proxy that fails does so silently and
        looks exactly like the agent having no data. Meanwhile "is the
        customer sharing their screen RIGHT NOW" is a stronger signal, is
        free, and is language-independent.

        Small talk never reaches here (_handle_turn returns early on any
        non-ANSWER intent), so this only ever fires on a real question. The
        cost of attaching a frame that turns out to be irrelevant is one
        vision call (~1.3s); the cost of missing one is the agent saying "I
        don't have information" while looking straight at the answer. Same
        asymmetry small_talk.py is built around.

        The TTL below is what keeps this honest: a frame is only attached
        while a share is genuinely live, so a stale frame can never be
        described as the current screen.
        """
        if self.state.latest_screen_frame is None:
            return False
        age = time.time() - self.state.latest_screen_frame_at
        if age > SCREEN_FRAME_TTL_SECONDS:
            log.info("orchestrator.screen_frame_stale", age_seconds=round(age, 1))
            self.state.latest_screen_frame = None
            return False
        log.info("orchestrator.screen_frame_attached", age_seconds=round(age, 1))
        return True

    async def _acknowledge(self, language: str) -> None:
        """Say "okay, let me look" before a visual turn does its extra work.

        A visual turn costs roughly 1.3s of vision on top of a normal one,
        and that time is silent — which reads as the agent having missed the
        question, so people repeat themselves and talk over the answer.

        Fired only on the vision path, because that is the only path with
        the extra second to cover. It is queued, not spoken over the answer:
        the adapter's say() calls play in order, so this lands first and the
        real answer follows it.

        Best-effort by design — a filler is a nicety, and losing it must
        never cost the caller their actual answer.
        """
        text = acknowledgement_for(language, self.state.visual_turns)
        self.state.visual_turns += 1
        try:
            await self.adapter.speak(text, language=language)
        except Exception as exc:  # noqa: BLE001
            log.warning("orchestrator.ack_failed", error=str(exc))
            return
        # Deliberately NOT written to the transcript: the transcript is the
        # record of what was asked and answered, and "one moment" is neither.
        log.info("orchestrator.ack_spoken", language=language, text=text)

    async def _filler_if_slow(self, language: str, answered: "asyncio.Event") -> bool:
        """Say something if the answer has not arrived in ACK_AFTER_SECONDS.

        Waits on the answer rather than sleeping blindly, so a fast turn
        cancels this before it ever speaks. The is_set() check after the
        timeout closes the last sliver of race — an answer landing in the
        moment between the timeout firing and this line would otherwise be
        followed by a "one sec" for a question already answered.
        """
        if ACK_AFTER_SECONDS <= 0:
            return False
        try:
            await asyncio.wait_for(answered.wait(), timeout=ACK_AFTER_SECONDS)
            return False
        except asyncio.TimeoutError:
            pass
        if answered.is_set():
            return False
        text = lookup_filler_for(language, self.state.filler_turns)
        self.state.filler_turns += 1
        try:
            await self.adapter.speak(text, language=language)
        except Exception as exc:  # noqa: BLE001 - a filler must never cost the answer
            log.warning("orchestrator.filler_failed", error=str(exc))
            return False
        self.state.filler_spoken_at = time.time()
        log.info("orchestrator.filler_spoken", language=language, text=text)
        return True

    def _language_for(self, question: str) -> str:
        if REPLY_LANGUAGE != "auto":
            return REPLY_LANGUAGE
        return detect_language(question, default=DEFAULT_LANGUAGE)

    async def _handle_turn(self, question: str, history: list[dict] | None = None) -> None:
        language = self._language_for(question)
        # Defaulting to the current state is for callers that drive a turn
        # directly (the tests, and the synthetic-speech harness) rather than
        # through on_speech; the live path always passes it explicitly.
        history = self.state.history() if history is None else history

        intent = classify(question)
        if intent is not Turn.ANSWER:
            await self._handle_small_talk(question, intent, language)
            return

        screen_image_b64 = None
        if self._wants_visual_context(question):
            # The brain-api does the actual vision call (app.core.llm.vision,
            # via OpenRouter) and folds the description into the evidence
            # set as a citable "screen" item — this orchestrator just hands
            # over the raw frame, it never fabricates a description itself.
            screen_image_b64 = base64.b64encode(self.state.latest_screen_frame).decode()
            await self._acknowledge(language)

        # The vision path already said something out loud before starting its
        # extra ~1.3s of work, so a second filler on top would just be the
        # agent talking to itself.
        answered = asyncio.Event()
        filler = (
            asyncio.create_task(self._filler_if_slow(language, answered))
            if screen_image_b64 is None
            else None
        )
        try:
            result = await self._ask_brain(question, screen_image_b64, language, history)
        except Exception as exc:  # noqa: BLE001 - a brain-api hiccup must not take the call down
            log.error("orchestrator.brain_api_error", error=str(exc))
            answered.set()
            if filler is not None:
                await filler  # reaped on the failure path too, not just the happy one
            await self._publish({"type": "turn.error", "error": str(exc)})
            await self.adapter.speak(
                "Sorry, I couldn't reach my knowledge base just now — could you ask again in a moment?"
            )
            return

        answered.set()
        filler_spoke = False
        if filler is not None:
            # Awaiting rather than cancelling: if it is mid-speak, letting it
            # finish keeps the filler and the answer in order. It returns
            # immediately in every other case.
            filler_spoke = await filler
        if filler_spoke:
            # Measured from when the filler was SPOKEN, not from now. A turn
            # that came back three seconds after the filler already has its
            # gap; sleeping a flat second there would delay the answer for no
            # reason. This only ever waits out the remainder.
            remaining = POST_FILLER_PAUSE_SECONDS - (time.time() - self.state.filler_spoken_at)
            if remaining > 0:
                await asyncio.sleep(remaining)

        if result is None:
            log.warning("orchestrator.no_turn_done_event", question=question)
            return

        answer = (result.get("answer") or "").strip()
        if await self._escalate_if_needed(question, result, history, language):
            return
        if answer:
            # Spoken text drops the [ev_xxx] markers — TTS reads them out
            # literally ("...platform ev 20021cda"). The structured answer
            # already went to the browser with every marker intact, so the
            # evidence chips are unaffected.
            spoken = for_speech(answer)
            # The SPOKEN form goes into memory, not the marked-up one: an
            # [ev_xxx] id is only valid for the turn that produced it, and a
            # remembered one would be reused this turn where it names nothing
            # (app.agent.history strips them server-side too — this just
            # means the wire never carries them in the first place).
            self.state.transcript.append(
                {"role": "agent", "speaker_id": self.agent_name, "text": spoken, "ts": time.time()}
            )
            await self._record_transcript("agent", self.agent_name, spoken)
            # The sound is added last, to the spoken copy only — the
            # transcript and the browser both keep the clean answer.
            await self.adapter.speak(
                with_thinking_sound(spoken, language, self.state.spoken_turns), language=language
            )
            self.state.spoken_turns += 1
        else:
            log.warning("orchestrator.empty_answer", question=question, result=result)

    # ── escalation ───────────────────────────────────────────────────────

    async def _escalate_if_needed(
        self, question: str, result: dict, history: list[dict], language: str
    ) -> bool:
        """Ask the brain-api whether this turn goes to the member. If so, say
        the holding line instead of the answer and wait in the background —
        the conversation carries on, and other questions are answered, while
        the member has their 60 seconds.

        Any failure here falls back to speaking the answer as it stands: an
        escalation is an improvement on an abstention, never a precondition
        for replying at all.
        """
        if not self.meeting_slug:
            return False
        try:
            resp = await self._http.post(
                f"{self.brain_api_url}/api/escalations/assess",
                json={"meeting_slug": self.meeting_slug, "question": question,
                      "result": {k: result.get(k) for k in ("answer", "confidence", "abstained", "escalation")},
                      "history": history[-6:], "turn": self._escalation_turns},
                timeout=5.0,
            )
            verdict = resp.json() if resp.status_code == 200 else {}
        except Exception as exc:  # noqa: BLE001
            log.warning("orchestrator.escalation_assess_failed", error=str(exc))
            return False
        if not verdict.get("escalate"):
            return False

        self._escalation_turns += 1
        holding = verdict.get("holding_line") or "Let me check on that and come back to you."
        log.info("orchestrator.escalated", id=verdict.get("id"), question=question)
        await self._publish({"type": "escalation.opened", "id": verdict.get("id"),
                             "question": question, "timeout_s": verdict.get("timeout_s")})
        self.state.transcript.append(
            {"role": "agent", "speaker_id": self.agent_name, "text": holding, "ts": time.time()}
        )
        await self._record_transcript("agent", self.agent_name, holding)
        await self.adapter.speak(holding, language=language)

        task = asyncio.create_task(
            self._await_escalation(verdict["id"], float(verdict.get("timeout_s") or 60), language)
        )
        self._escalations.add(task)
        task.add_done_callback(self._escalations.discard)
        return True

    async def _await_escalation(self, escalation_id: str, timeout_s: float, language: str) -> None:
        """Poll until the brain-api says the member answered, declined, or
        ran out of time — it owns the clock, so this never decides expiry
        itself. The hard stop below only guards against an API that stops
        answering altogether."""
        deadline = time.monotonic() + timeout_s + 15
        while time.monotonic() < deadline:
            await asyncio.sleep(ESCALATION_POLL_SECONDS)
            try:
                resp = await self._http.get(
                    f"{self.brain_api_url}/api/escalations/{escalation_id}/status", timeout=5.0
                )
                if resp.status_code != 200:
                    continue
                status = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.warning("orchestrator.escalation_poll_failed", error=str(exc))
                continue
            if status.get("status") == "open":
                continue
            line = (status.get("say") or "").strip()
            log.info("orchestrator.escalation_resolved", id=escalation_id, status=status.get("status"))
            await self._publish({"type": "escalation.resolved", "id": escalation_id,
                                 "status": status.get("status")})
            if line:
                spoken = for_speech(line)
                self.state.transcript.append(
                    {"role": "agent", "speaker_id": self.agent_name, "text": spoken, "ts": time.time()}
                )
                await self._record_transcript("agent", self.agent_name, spoken)
                await self.adapter.speak(spoken, language=language)
            return
        log.warning("orchestrator.escalation_poll_gave_up", id=escalation_id)

    async def _handle_small_talk(self, question: str, intent: Turn, language: str) -> None:
        """Greetings get an instant canned line; ambient speech gets
        nothing at all. Neither makes a factual claim, so the "no uncited
        claim" rule is untouched — there is nothing here to cite."""
        log.info("orchestrator.small_talk", intent=intent.value, text=question, language=language)
        turn_id = f"{int(time.time() * 1000)}"
        # Pre-written per language rather than generated — a greeting has
        # to be instant, and there is no LLM in this path at all.
        answer = greeting_for(language) if intent is Turn.GREETING else ""

        # Still traced, so the advanced panel shows a deliberate 0ms fast
        # path rather than going blank as if the agent had missed the turn.
        await self._publish({"type": "turn.requested", "turn_id": turn_id, "question": question, "source": "voice"})
        await self._publish({"type": "turn.fastpath", "turn_id": turn_id, "source": "voice", "t": 0,
                             "intent": intent.value, "language": language})
        await self._publish({"type": "turn.done", "turn_id": turn_id, "source": "voice", "t": 0, "ms": 0,
                             "result": {"answer": answer or "(no reply — ambient speech)", "claims": [],
                                        "confidence": "high", "abstained": False, "escalation": None,
                                        "tool_trace": []}})
        if answer:
            await self._record_transcript("agent", self.agent_name, answer)
            await self.adapter.speak(answer, language=language)

    async def _ask_brain(
        self,
        question: str,
        screen_image_b64: str | None,
        language: str = DEFAULT_LANGUAGE,
        history: list[dict] | None = None,
    ) -> dict | None:
        """Stream one turn from the brain-api, forwarding every trace event
        into the room as it arrives, and return the final answer.

        Uses /api/agent/ask/stream rather than /api/agent/ask so the
        browser's advanced panel can show which tool is running WHILE it
        runs — a plain POST only reveals the tool trace once the whole
        turn (often tens of seconds, per the documented DeepSeek latency
        variance) is already over. The spoken answer is identical either
        way; this only changes when the UI hears about the steps.
        """
        turn_id = f"{int(time.time() * 1000)}"
        await self._publish({"type": "turn.requested", "turn_id": turn_id, "question": question,
                             "source": "voice", "language": language})

        final: dict | None = None
        async with self._http.stream(
            "POST",
            f"{self.brain_api_url}/api/agent/ask/stream",
            json={
                "question": question,
                "screen_image_base64": screen_image_b64,
                "language": language,
                "meeting_slug": self.meeting_slug,
                "history": history or [],
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    log.warning("orchestrator.bad_trace_event", line=line[:200])
                    continue
                event["turn_id"] = turn_id
                event["source"] = "voice"
                await self._publish(event)
                if event.get("type") == "turn.done":
                    final = event.get("result")
        return final

    async def _publish(self, event: dict) -> None:
        # Best-effort: the panel is observability, never a precondition for
        # answering. An adapter that can't publish (or a transport with no
        # data channel at all) must not break the turn.
        try:
            await self.adapter.publish_event(event)
        except Exception as exc:  # noqa: BLE001
            log.warning("orchestrator.publish_event_failed", error=str(exc))
