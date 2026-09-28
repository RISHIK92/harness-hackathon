"""Is a voice worker actually running?

    POST /api/agent-worker/heartbeat   the call-agent worker, every ~10s
    GET  /api/agent-worker/status      the call screens, before and during a call

Without this, "the agent didn't join" had no visible cause: a call opened,
nobody came, and nothing said why. The usual reason is simply that no worker
process is running (or it is pointed at a different API), and that is the one
thing the browser cannot find out on its own — LiveKit does not list workers.

The heartbeat needs the worker's token (app/core/service_auth.py, scope
"worker:heartbeat"), like its other calls. The status read stays public —
the join page asks it before anyone signs in — but no longer names the
worker's host.
"""
from __future__ import annotations

import json
import time

import redis.asyncio as aioredis
from typing import Optional

from fastapi import APIRouter, Depends
from sqlmodel import SQLModel

from app.config import get_settings
from app.core import service_auth

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
async def heartbeat(body: Heartbeat,
                    worker_token: Optional[str] = Depends(service_auth.worker_token_header)):
    service_auth.require_worker(worker_token, service_auth.HEARTBEAT_SCOPE)
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
    return {"online": True, "agent_name": agent_name,
            "seconds_ago": round(time.time() - data.get("at", 0), 1)}
