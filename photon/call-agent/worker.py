"""Entrypoint. `python3 worker.py dev` (or `start` for prod) joins whatever
LiveKit room this worker gets dispatched to, wires the LiveKit adapter to a
fresh Orchestrator per job, and runs until the room closes.

Reference: LiveKit's own Agents quickstart shapes the entrypoint/
WorkerOptions boilerplate below (job dispatch, connect, run-forever) — this
file is our own, written against the actual 1.7 API, not copied from it.
"""
from __future__ import annotations

import asyncio
import os

import httpx
import structlog
from dotenv import load_dotenv
from livekit import agents, rtc

import service_auth
from adapters.livekit_adapter import LiveKitAdapter, build_stt
from adapters.room_listener import RoomListener
from orchestrator import Orchestrator

load_dotenv()
log = structlog.get_logger()

BRAIN_API_URL = os.environ.get("BRAIN_API_URL", "http://localhost:8000")


CALL_CONFIG_ATTEMPTS = 3


async def _call_config(room_name: str) -> dict:
    """Fetch the meeting's configuration before building the session.

    The voice stack has to be decided BEFORE AgentSession is constructed —
    STT and TTS are constructor arguments — so this is a blocking fetch at
    job start rather than something applied later.

    A few retries, then a FAIL-SAFE default: listen-only. It used to fall
    back to `{}`, which meant speak mode — so a brain-api blip at join time
    turned a meeting set up as whisper (the agent must never speak) into one
    where it announced itself and answered aloud. Not speaking when unsure
    is the recoverable mistake; speaking into a whisper call is not.
    """
    headers = service_auth.headers_for(service_auth.meeting_scope(room_name))
    for attempt in range(CALL_CONFIG_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(f"{BRAIN_API_URL}/api/meetings/{room_name}/call-config",
                                        headers=headers)
                if resp.status_code == 200:
                    return resp.json()
                log.info("worker.call_config_unavailable", status=resp.status_code, room=room_name)
                if resp.status_code in (401, 404, 503):
                    break               # not transient: wrong credentials, or no such meeting
        except Exception as exc:  # noqa: BLE001
            log.warning("worker.call_config_failed", error=str(exc), room=room_name, attempt=attempt + 1)
        await asyncio.sleep(1.5 * (attempt + 1))
    log.error("worker.call_config_unresolved_listen_only", room=room_name)
    return {"mode": "whisper"}


async def entrypoint(ctx: agents.JobContext) -> None:
    log.info("worker.job_starting", room=ctx.room.name)
    config = await _call_config(ctx.room.name)
    voice_stack = config.get("voice_stack")
    if voice_stack:
        log.info("worker.voice_stack_from_call", stack=voice_stack, room=ctx.room.name)

    orchestrator: Orchestrator | None = None
    adapter: LiveKitAdapter | RoomListener | None = None
    try:
        # Orchestrator needs a TransportAdapter reference before it exists,
        # and the adapter needs an object implementing SessionCallbacks
        # before IT exists — break the cycle by handing the orchestrator a
        # thin forwarding shim, then pointing it at the real adapter once
        # LiveKitAdapter.start() has constructed the AgentSession.
        class _Callbacks:
            async def on_speech(self, text: str, speaker_id: str, is_final: bool) -> None:
                await orchestrator.on_speech(text, speaker_id, is_final)

            async def on_frame(self, image: bytes, source: str) -> None:
                await orchestrator.on_frame(image, source)

            async def on_poke(self, speaker_id: str, display_name: str) -> None:
                await orchestrator.on_poke(speaker_id, display_name)

        whisper = config.get("mode") == "whisper"
        adapter = RoomListener(ctx, _Callbacks(), lambda: build_stt(voice_stack)) if whisper else LiveKitAdapter(
            ctx, _Callbacks(), voice_stack=voice_stack,
            org_name=config.get("org_name"),
            # "Priya's Photon" when it attends for a member; the plain name
            # when it represents the company.
            agent_name=config.get("display_name") or config.get("agent_name"),
            poc_name=config.get("poc_name") if config.get("attends_as", "member") == "member" else None,
        )
        # The room name is the meeting slug (abcd-efgh), which is what the
        # transcript is filed under.
        orchestrator = Orchestrator(
            adapter, BRAIN_API_URL, meeting_slug=ctx.room.name,
            agent_name=config.get("agent_name"),
            # Whisper mode: transcribe the whole room, never speak.
            listen_only=whisper,
        )

        await adapter.start()
        log.info("worker.session_started", room=ctx.room.name)

        # BUG (caught live, not in review): adapter.start() returns as soon
        # as the AgentSession is up and the announcement has played — it
        # does not block for the life of the call. Without this wait,
        # falling through to `finally` immediately closed the
        # orchestrator's httpx client while the session kept running in
        # the background, so every real question after startup failed with
        # "Cannot send a request, as the client has been closed." and the
        # agent spoke the fallback error instead of a real answer. Block
        # here until the room actually disconnects.
        disconnected = asyncio.Event()
        ctx.room.on("disconnected", lambda *_: disconnected.set())
        if ctx.room.connection_state != rtc.ConnectionState.CONN_CONNECTED:
            disconnected.set()
        await disconnected.wait()
        log.info("worker.room_disconnected", room=ctx.room.name)

    except Exception:
        log.exception("worker.job_failed", room=ctx.room.name)
        raise
    finally:
        # The whisper listener holds one STT stream per participant; they
        # must not outlive the room.
        if isinstance(adapter, RoomListener):
            await adapter.close()
        if orchestrator:
            await orchestrator.close()


# The name this worker registers under, and the name every room asks for.
#
# Unnamed, a worker is AUTO-dispatched: LiveKit assigns it only at the moment
# a room is created. A worker that starts late, restarts, or crashes mid-call
# therefore never reaches a room that already exists — the call just has no
# agent, with nothing to say why. Named, it is assigned by explicit dispatch,
# which the join-token route (client/app/api/livekit-token) requests for
# every join, so a worker that comes up later is still sent to the call.
AGENT_NAME = os.environ.get("AGENT_DISPATCH_NAME", "photon")
HEARTBEAT_SECONDS = 10


def _heartbeat_forever() -> None:
    """Tell the brain-api this worker is alive, so the call screens can say
    "voice worker offline" instead of silently waiting for an agent.

    A plain thread with blocking HTTP: it must keep beating while the agent
    runtime owns the event loop, and one failed beat only costs a status
    light — never the worker.
    """
    import socket
    import time
    import uuid

    body = {"agent_name": AGENT_NAME, "worker_id": uuid.uuid4().hex[:8],
            "host": socket.gethostname(), "brain_api_url": BRAIN_API_URL}
    while True:
        try:
            httpx.post(f"{BRAIN_API_URL}/api/agent-worker/heartbeat", json=body, timeout=5.0,
                       headers=service_auth.headers_for(service_auth.HEARTBEAT_SCOPE))
        except Exception as exc:  # noqa: BLE001
            log.warning("worker.heartbeat_failed", error=str(exc)[:120], brain_api=BRAIN_API_URL)
        time.sleep(HEARTBEAT_SECONDS)


if __name__ == "__main__":
    import threading

    threading.Thread(target=_heartbeat_forever, name="heartbeat", daemon=True).start()
    log.info("worker.registering", agent_name=AGENT_NAME, brain_api=BRAIN_API_URL)
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=AGENT_NAME,
            ws_url=os.environ.get("LIVEKIT_URL"),
            api_key=os.environ.get("LIVEKIT_API_KEY"),
            api_secret=os.environ.get("LIVEKIT_API_SECRET"),
        )
    )
