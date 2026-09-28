"""Phase 0 on the worker side (ENTERPRISE_ARCHITECTURE.md Appendix A).

  D-08/D-09  every brain-api call carries this meeting's worker token
  D-10       trace events (which carry internal evidence) reach members only,
             and are never broadcast when no member is present
  D-44       a failed turn is never silent; an unreachable brain-api at join
             time never turns a whisper meeting into a speaking one
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
from types import SimpleNamespace

import httpx

import service_auth
import worker as worker_module
from adapters.livekit_adapter import LiveKitAdapter
from orchestrator import Orchestrator


class _Adapter:
    def __init__(self):
        self.spoken = []

    async def speak(self, text, language=None):
        self.spoken.append(text)

    async def cancel_speech(self): pass
    async def announce(self, text, language=None): pass
    async def publish_event(self, event): pass


def test_every_brain_call_carries_the_meetings_worker_token(monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk-secret-for-tests")
    monkeypatch.delenv("WORKER_SERVICE_TOKEN", raising=False)
    orch = Orchestrator(adapter=_Adapter(), brain_api_url="http://brain", meeting_slug="abcd-efgh")
    expected = service_auth.token_for(service_auth.meeting_scope("abcd-efgh"))
    assert expected and orch._http.headers.get(service_auth.HEADER) == expected
    other = service_auth.token_for(service_auth.meeting_scope("wxyz-2345"))
    assert other != expected


def test_a_turn_that_ends_without_an_answer_is_not_silent():
    adapter = _Adapter()
    orch = Orchestrator(adapter=adapter, brain_api_url="http://brain", meeting_slug="abcd-efgh")

    async def no_answer(question, screen, language, history):
        return None                       # the stream ended on turn.error
    orch._ask_brain = no_answer
    asyncio.run(orch._handle_turn("how do webhooks retry?", "user:1"))
    assert adapter.spoken and "ask again" in adapter.spoken[-1]


def test_an_unreachable_brain_api_at_join_means_listen_only(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("down")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(worker_module.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(refuse), **kw))

    async def no_wait(_):
        return None
    monkeypatch.setattr(worker_module.asyncio, "sleep", no_wait)
    config = asyncio.run(worker_module._call_config("abcd-efgh"))
    assert config.get("mode") == "whisper"


# ── trace events: members only ───────────────────────────────────────────

def _participant(identity, meta):
    return SimpleNamespace(identity=identity, metadata=json.dumps(meta) if meta is not None else "")


def _adapter_with(participants):
    sent = []

    class _Local:
        async def publish_data(self, payload, reliable=True, topic="", destination_identities=()):
            sent.append(list(destination_identities))

    room = SimpleNamespace(remote_participants={p.identity: p for p in participants},
                           local_participant=_Local())
    adapter = LiveKitAdapter.__new__(LiveKitAdapter)
    adapter._ctx = SimpleNamespace(room=room)
    return adapter, sent


def test_trace_events_go_to_members_only():
    adapter, sent = _adapter_with([
        _participant("user:member", {"member": True}),
        _participant("user:outsider", {"member": False}),
        _participant("guest:abc", {"guest": True, "member": False}),
        _participant("user:old-token", {"guest": False}),          # pre-change token: no claim
    ])
    asyncio.run(adapter.publish_event({"type": "turn.done", "result": {"tool_trace": []}}))
    assert sent == [["user:member"]]


def test_no_member_in_the_room_means_nothing_is_sent():
    """An empty destination list means EVERYONE to LiveKit — the leak itself."""
    adapter, sent = _adapter_with([_participant("guest:abc", {"guest": True, "member": False})])
    asyncio.run(adapter.publish_event({"type": "turn.done"}))
    assert sent == []
