"""Is a voice worker actually running?

    POST /api/agent-worker/heartbeat   the call-agent worker, every ~10s
    GET  /api/agent-worker/status      the call screens, before and during a call

Without this, "the agent didn't join" had no visible cause: a call opened,
nobody came, and nothing said why. The usual reason is simply that no worker
process is running (or it is pointed at a different API), and that is the one
thing the browser cannot find out on its own — LiveKit does not list workers.

The heartbeat is unauthenticated, like the worker's transcript writes: a
worker is a server-side process with no user session. The worst a forged
heartbeat can do is make a status light green for 30 seconds.
"""
from __future__ import annotations

import json
import time

import redis.asyncio as aioredis
from fastapi import APIRouter
from sqlmodel import SQLModel

from app.config import get_settings

router = APIRouter()

TTL_S = 30  # three missed heartbeats and the worker counts as gone
_KEY = "agent_worker:{name}"


class Heartbeat(SQLModel):
    agent_name: str = "photon"
    worker_id: str = ""
    host: str = ""
    brain_api_url: str = ""


def _redis() -> aioredis.Redis:
    return aioredis.from_url(get_settings().redis_url, decode_responses=True)


@router.post("/heartbeat")
async def heartbeat(body: Heartbeat):
    r = _redis()
    try:
        await r.set(_KEY.format(name=body.agent_name or "photon"),
                    json.dumps({**body.model_dump(), "at": time.time()}), ex=TTL_S)
    finally:
        await r.aclose()
    return {"ok": True}


@router.get("/status")
async def status(agent_name: str = "photon"):
    r = _redis()
    try:
        raw = await r.get(_KEY.format(name=agent_name))
    finally:
        await r.aclose()
    if not raw:
        return {"online": False, "agent_name": agent_name,
                "detail": "No voice worker is running — start it with scripts/dev.sh"}
    data = json.loads(raw)
    return {"online": True, "agent_name": agent_name, "host": data.get("host"),
            "seconds_ago": round(time.time() - data.get("at", 0), 1)}
