"""The worker's half of conversation memory: TurnState.transcript, written
since Phase 4 and until now never read, becomes the history sent with each
question. No LiveKit, no network — a mock adapter and a stubbed brain call.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator import HISTORY_TURNS, Orchestrator  # noqa: E402


class MockAdapter:
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


def make(monkeypatched_answer="Bangalore has a partner rate [ev_80abd768]."):
    orch = Orchestrator(MockAdapter(), "http://brain.invalid")
    sent = []

    async def fake_ask(question, screen_image_b64, language="en-IN", history=None):
        sent.append({"question": question, "history": history})
        return {"answer": monkeypatched_answer, "claims": [], "confidence": "high",
                "abstained": False, "escalation": None, "tool_trace": []}

    orch._ask_brain = fake_ask
    orch._record_transcript = lambda *a, **k: asyncio.sleep(0)
    return orch, sent


def test_first_question_carries_no_history():
    orch, sent = make()
    asyncio.run(orch.on_speech("why does pricing differ in Bangalore", "user-a", True))
    assert sent[0]["history"] == []


def test_the_current_question_is_not_its_own_history():
    """Read before the utterance is appended. Included, it would hand the
    model the question it is being asked as though it were the antecedent."""
    orch, sent = make()
    asyncio.run(orch.on_speech("why does pricing differ in Bangalore", "user-a", True))
    asyncio.run(orch.on_speech("why is that?", "user-a", True))
    texts = [t["text"] for t in sent[1]["history"]]
    assert "why is that?" not in texts
    assert "why does pricing differ in Bangalore" in texts


def test_the_agents_own_answer_is_remembered():
    """"is that documented?" refers to what was ANSWERED, not to what was
    asked — a question-only history would lose the referent."""
    orch, sent = make()
    asyncio.run(orch.on_speech("why does pricing differ in Bangalore", "user-a", True))
    asyncio.run(orch.on_speech("is that documented?", "user-a", True))
    assert [t["role"] for t in sent[1]["history"]] == ["user", "agent"]
    assert "partner rate" in sent[1]["history"][1]["text"]


def test_remembered_answers_carry_no_citation_markers():
    """An [ev_xxx] id is valid only for the turn that produced it; reused a
    turn later it names nothing and the verifier strips the claim."""
    orch, sent = make()
    asyncio.run(orch.on_speech("why does pricing differ", "user-a", True))
    asyncio.run(orch.on_speech("is that documented?", "user-a", True))
    assert "ev_" not in sent[1]["history"][1]["text"]


def test_every_speaker_is_remembered_not_just_the_asker():
    """On a multi-party call the referent of "why is that?" is often
    something a COLLEAGUE said."""
    orch, sent = make()
    asyncio.run(orch.on_speech("why does pricing differ in Bangalore", "alice", True))
    asyncio.run(orch.on_speech("and why is that?", "bob", True))
    assert "why does pricing differ in Bangalore" in [t["text"] for t in sent[1]["history"]]


def test_history_is_capped():
    orch, sent = make()
    for i in range(10):
        asyncio.run(orch.on_speech(f"question number {i}", "user-a", True))
    assert len(sent[-1]["history"]) == HISTORY_TURNS


def test_small_talk_never_reaches_the_brain():
    """The 0ms gate still short-circuits first — memory must not have
    turned a greeting back into a pipeline turn."""
    orch, sent = make()
    asyncio.run(orch.on_speech("hello, how are you", "user-a", True))
    assert sent == []
