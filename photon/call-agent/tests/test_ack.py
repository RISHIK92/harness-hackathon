"""The spoken acknowledgement on a visual turn.

Guards the three things that make it useful rather than annoying: it fires
only when a frame is actually attached, it comes out in the caller's
language, and it rotates instead of repeating one line forever.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import pytest
from orchestrator import Orchestrator, SCREEN_FRAME_TTL_SECONDS
from language import ACKNOWLEDGEMENTS, acknowledgement_for


class _RecordingAdapter:
    def __init__(self):
        self.spoken = []

    async def speak(self, text, language=None):
        self.spoken.append((language, text))

    async def cancel_speech(self): pass
    async def announce(self, text): pass
    async def publish_event(self, event): pass


def _orch(adapter=None):
    return Orchestrator(adapter=adapter or _RecordingAdapter(),
                        brain_api_url="http://localhost:8000")


def test_every_language_has_acknowledgements():
    # A missing language would silently fall back to English mid-call, which
    # is worse than no filler at all.
    for code in ("en-IN", "hi-IN", "te-IN", "ta-IN"):
        assert ACKNOWLEDGEMENTS.get(code), f"no acknowledgements for {code}"


def test_unknown_language_falls_back_to_english():
    assert acknowledgement_for("fr-FR") in ACKNOWLEDGEMENTS["en-IN"]


@pytest.mark.parametrize("code", ["en-IN", "hi-IN", "te-IN", "ta-IN"])
def test_variants_rotate_and_never_repeat_back_to_back(code):
    seen = [acknowledgement_for(code, i) for i in range(len(ACKNOWLEDGEMENTS[code]))]
    assert len(set(seen)) == len(seen), f"duplicate variants for {code}"
    assert acknowledgement_for(code, 0) != acknowledgement_for(code, 1)


def test_acknowledgement_is_spoken_in_the_callers_language():
    a = _RecordingAdapter()
    o = _orch(a)
    asyncio.run(o._acknowledge("te-IN"))
    assert len(a.spoken) == 1
    language, text = a.spoken[0]
    assert language == "te-IN"
    assert text in ACKNOWLEDGEMENTS["te-IN"]


def test_consecutive_visual_turns_rotate():
    a = _RecordingAdapter()
    o = _orch(a)
    asyncio.run(o._acknowledge("en-IN"))
    asyncio.run(o._acknowledge("en-IN"))
    assert a.spoken[0][1] != a.spoken[1][1]


def test_acknowledgement_is_kept_out_of_the_transcript():
    # It is filler, not content: the transcript records what was asked and
    # answered, and this is neither.
    a = _RecordingAdapter()
    o = _orch(a)
    asyncio.run(o._acknowledge("en-IN"))
    assert o.state.transcript == []


def test_a_failing_ack_never_costs_the_answer():
    class Broken(_RecordingAdapter):
        async def speak(self, text, language=None):
            raise RuntimeError("tts down")

    o = _orch(Broken())
    asyncio.run(o._acknowledge("en-IN"))  # must not raise


def test_no_acknowledgement_without_a_live_share():
    # The ack rides on _wants_visual_context, so a turn with no fresh frame
    # must stay completely silent until the real answer arrives.
    o = _orch()
    assert o._wants_visual_context("what's on my screen") is False

    asyncio.run(o.on_frame(b"screen-bytes", "screen"))
    o.state.latest_screen_frame_at = time.time() - (SCREEN_FRAME_TTL_SECONDS + 5)
    assert o._wants_visual_context("what's on my screen") is False


# ── No dead air on an ordinary (non-visual) turn ──────────────────────────
# A turn is ~2.1-2.9s of silence. On a call that reads as the agent having
# missed the question, so people repeat themselves and talk over the answer.

import asyncio  # noqa: E402

import orchestrator as orch_mod  # noqa: E402
from language import LOOKUP_FILLERS  # noqa: E402
from speech import THINKING_SOUNDS  # noqa: E402


def _is_answer(spoken: str) -> bool:
    """The answer, with or without a rotating thinking-sound prefix."""
    return spoken.lower().endswith("postgres, via prisma.")


class _SlowAdapter:
    def __init__(self):
        self.spoken = []

    async def speak(self, text, language=None):
        self.spoken.append(text)

    async def cancel_speech(self):
        pass

    async def announce(self, text):
        pass

    async def publish_event(self, event):
        pass


def _orch_with(answer_delay, monkeypatch=None):
    o = orch_mod.Orchestrator(_SlowAdapter(), "http://brain.invalid")
    o._record_transcript = lambda *a, **k: asyncio.sleep(0)

    async def fake_ask(question, screen_b64, language="en-IN", history=None):
        await asyncio.sleep(answer_delay)
        return {"answer": "Postgres, via Prisma.", "claims": [], "confidence": "high",
                "abstained": False, "escalation": None, "tool_trace": []}

    o._ask_brain = fake_ask
    return o


def test_a_slow_turn_says_something_before_the_answer():
    o = _orch_with(answer_delay=0.25)
    orch_mod.ACK_AFTER_SECONDS = 0.05
    asyncio.run(o._handle_turn("what database does it use?"))
    assert o.adapter.spoken[0] in LOOKUP_FILLERS["en-IN"]
    assert _is_answer(o.adapter.spoken[-1])


def test_a_fast_turn_stays_quiet():
    """Otherwise every trivial question gets a pointless "one sec" in front."""
    o = _orch_with(answer_delay=0.0)
    orch_mod.ACK_AFTER_SECONDS = 0.5
    asyncio.run(o._handle_turn("what database does it use?"))
    assert len(o.adapter.spoken) == 1 and _is_answer(o.adapter.spoken[0])


def test_the_filler_never_lands_after_the_answer():
    """The failure this ordering guards: a "one sec" spoken for a question
    that has already been answered."""
    o = _orch_with(answer_delay=0.05)
    orch_mod.ACK_AFTER_SECONDS = 0.05
    asyncio.run(o._handle_turn("what database does it use?"))
    assert _is_answer(o.adapter.spoken[-1])


def test_it_can_be_switched_off():
    o = _orch_with(answer_delay=0.2)
    orch_mod.ACK_AFTER_SECONDS = 0
    asyncio.run(o._handle_turn("what database does it use?"))
    assert len(o.adapter.spoken) == 1 and _is_answer(o.adapter.spoken[0])


def test_the_filler_rotates_so_it_does_not_sound_canned():
    o = _orch_with(answer_delay=0.15)
    orch_mod.ACK_AFTER_SECONDS = 0.02
    asyncio.run(o._handle_turn("one?"))
    asyncio.run(o._handle_turn("two?"))
    fillers = [t for t in o.adapter.spoken if t in LOOKUP_FILLERS["en-IN"]]
    assert len(fillers) == 2 and fillers[0] != fillers[1]


def test_lookup_fillers_never_claim_to_be_looking_at_a_screen():
    """ACKNOWLEDGEMENTS says "checking your screen" — true on the vision path,
    a small lie on any other turn, and exactly the class of thing the
    grounding rules exist to prevent."""
    for variants in LOOKUP_FILLERS.values():
        for text in variants:
            assert "screen" not in text.lower()


# ── The answer does not tread on the heels of the filler ─────────────────


def test_the_answer_waits_after_a_filler_was_spoken():
    """Without the hold, "checking, one moment" and the answer are enqueued in
    the same instant and read as one run-on sentence — worse than the silence
    the filler was there to fill."""
    o = _orch_with(answer_delay=0.05)
    orch_mod.ACK_AFTER_SECONDS = 0.02
    orch_mod.POST_FILLER_PAUSE_SECONDS = 0.3
    start = asyncio.get_event_loop_policy().new_event_loop().time
    import time as _t
    t0 = _t.monotonic()
    asyncio.run(o._handle_turn("what database does it use?"))
    elapsed = _t.monotonic() - t0
    assert elapsed >= 0.3, f"answer was not held after the filler ({elapsed:.2f}s)"
    assert o.adapter.spoken[0] in LOOKUP_FILLERS["en-IN"]
    assert _is_answer(o.adapter.spoken[-1])


def test_no_hold_when_no_filler_was_spoken():
    """A fast turn must not pay the pause."""
    o = _orch_with(answer_delay=0.0)
    orch_mod.ACK_AFTER_SECONDS = 0.5
    orch_mod.POST_FILLER_PAUSE_SECONDS = 2.0
    import time as _t
    t0 = _t.monotonic()
    asyncio.run(o._handle_turn("what database does it use?"))
    assert _t.monotonic() - t0 < 0.5


# ── Thinking sounds ──────────────────────────────────────────────────────


def test_a_thinking_sound_is_spoken_but_never_recorded():
    """It is glued on at the speech layer, so the transcript and the browser
    keep the clean answer — and, critically, it cannot reach back and change
    the verdict the way a prompt-level opener licence did."""
    o = _orch_with(answer_delay=0.0)
    orch_mod.ACK_AFTER_SECONDS = 0
    recorded = []
    o._record_transcript = lambda role, name, text, ident=None: (recorded.append(text), asyncio.sleep(0))[1]
    o.state.spoken_turns = 0  # "Hmm," is first in the rotation
    asyncio.run(o._handle_turn("what database does it use?"))
    assert o.adapter.spoken[-1].startswith("Hmm,")
    assert recorded[-1] == "Postgres, via Prisma."


def test_thinking_sounds_do_not_land_on_every_turn():
    """A sound on every single turn is its own tic — the rotation has to
    include silence."""
    assert "" in THINKING_SOUNDS["en-IN"]
    for lang, sounds in THINKING_SOUNDS.items():
        assert any(s == "" for s in sounds), lang


def test_a_late_answer_is_not_held_any_further():
    """The gap is measured from when the filler was spoken. An answer that
    arrives well after it already HAS its gap — sleeping again would delay it
    for no reason."""
    o = _orch_with(answer_delay=0.6)
    orch_mod.ACK_AFTER_SECONDS = 0.05
    orch_mod.POST_FILLER_PAUSE_SECONDS = 0.2
    import time as _t
    t0 = _t.monotonic()
    asyncio.run(o._handle_turn("what database does it use?"))
    # filler at 0.05s + 0.2s gap = 0.25s, but the answer only lands at 0.6s,
    # so the total must stay at the answer's own latency, not 0.8s.
    assert _t.monotonic() - t0 < 0.75
