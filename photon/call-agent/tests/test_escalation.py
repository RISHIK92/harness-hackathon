"""Live escalation on the call path, against a fake brain-api.

What is guarded: an escalated turn says the holding line INSTEAD of the
abstention, the answer is relayed when the member replies, the apology is
spoken when they don't, and any failure to reach the escalation endpoint
falls back to speaking the answer as it stands.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json

import httpx

import orchestrator as orch_module
from orchestrator import Orchestrator


class _RecordingAdapter:
    def __init__(self):
        self.spoken = []
        self.events = []

    async def speak(self, text, language=None):
        self.spoken.append(text)

    async def cancel_speech(self): pass
    async def announce(self, text): pass

    async def publish_event(self, event):
        self.events.append(event)


ABSTAINED = {"answer": "I don't have evidence for that.", "confidence": "low",
             "abstained": True, "escalation": None, "claims": [], "tool_trace": []}


def _orch(handler):
    a = _RecordingAdapter()
    o = Orchestrator(adapter=a, brain_api_url="http://brain", meeting_slug="abcd-efgh",
                     agent_name="Photon")
    o._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def fake_ask(question, screen, language, history):
        return ABSTAINED
    o._ask_brain = fake_ask
    return o, a


def _brain(statuses, escalate=True):
    polls = iter(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/assess"):
            body = json.loads(request.content)
            assert body["meeting_slug"] == "abcd-efgh"
            if not escalate:
                return httpx.Response(200, json={"escalate": False})
            return httpx.Response(200, json={"escalate": True, "id": "e1", "timeout_s": 1,
                                             "holding_line": "Let me check with Priya."})
        if path.endswith("/status"):
            return httpx.Response(200, json=next(polls))
        return httpx.Response(201, json={"ok": True})     # transcript writes
    return handler


async def _run(o, question="what discount can you do on a two year deal?"):
    await o._handle_turn(question, history=[])
    await asyncio.gather(*list(o._escalations))


def test_holding_line_then_the_members_answer(monkeypatch):
    monkeypatch.setattr(orch_module, "ESCALATION_POLL_SECONDS", 0.01)
    o, a = _orch(_brain([{"status": "open"},
                         {"status": "answered", "say": "Coming back to that — Priya says: 15% off."}]))
    asyncio.run(_run(o))
    assert a.spoken[0] == "Let me check with Priya."
    assert "I don't have evidence" not in " ".join(a.spoken)
    assert a.spoken[-1].endswith("Priya says: 15% off.")
    assert [e["type"] for e in a.events if e["type"].startswith("escalation")] == \
        ["escalation.opened", "escalation.resolved"]


def test_no_reply_means_an_apology(monkeypatch):
    monkeypatch.setattr(orch_module, "ESCALATION_POLL_SECONDS", 0.01)
    o, a = _orch(_brain([{"status": "expired",
                          "say": "Sorry — I couldn't reach Priya; you'll get it in writing today."}]))
    asyncio.run(_run(o))
    assert a.spoken == ["Let me check with Priya.",
                        "Sorry — I couldn't reach Priya; you'll get it in writing today."]


def test_not_escalated_speaks_the_answer():
    o, a = _orch(_brain([], escalate=False))
    asyncio.run(_run(o))
    assert len(a.spoken) == 1 and "don't have evidence" in a.spoken[0]


def test_escalation_endpoint_down_falls_back_to_the_answer():
    def handler(request):
        if request.url.path.endswith("/assess"):
            raise httpx.ConnectError("down")
        return httpx.Response(201, json={})
    o, a = _orch(handler)
    asyncio.run(_run(o))
    assert len(a.spoken) == 1 and "don't have evidence" in a.spoken[0]


def test_no_meeting_no_escalation():
    o, a = _orch(_brain([]))
    o.meeting_slug = None
    asyncio.run(_run(o))
    assert len(a.spoken) == 1
