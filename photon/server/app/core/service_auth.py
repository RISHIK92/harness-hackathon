"""Service-to-service credentials for the call-agent worker.

The worker is a server-side process with no user session, so every route it
calls (transcript, call-config, escalations, the agent ask stream, the
heartbeat) used to be open to anyone who knew a meeting code. It now proves
itself with a token SCOPED TO ONE MEETING:

    token(scope) = HMAC-SHA256(worker_key, scope)      scope = "meeting:<slug>"

so a token captured from one call is useless against any other call, and the
worker needs no per-meeting provisioning: it derives the token from the room
name it was dispatched to.

`worker_key` is WORKER_SERVICE_TOKEN when set. Otherwise it is derived from
LIVEKIT_API_SECRET, which the worker, this API and the Next token route
already share — anyone holding it can already mint LiveKit room tokens, so
deriving from it adds no new holder and needs no new configuration. With
neither set the worker routes refuse (503) rather than fall open.

The same derivation lives in call-agent/service_auth.py; the two must agree.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Optional

from fastapi import Header, HTTPException, status

from app.config import get_settings

HEADER = "X-Worker-Token"
HEARTBEAT_SCOPE = "worker:heartbeat"


def meeting_scope(slug: str) -> str:
    from app.services.meeting_slug import normalise

    return f"meeting:{normalise(slug)}"


def _worker_key() -> Optional[bytes]:
    settings = get_settings()
    if settings.worker_service_token:
        return settings.worker_service_token.encode()
    if settings.livekit_api_secret:
        return hmac.new(settings.livekit_api_secret.encode(), b"photon-worker-v1", hashlib.sha256).digest()
    return None


def token_for(scope: str) -> Optional[str]:
    key = _worker_key()
    if key is None:
        return None
    return hmac.new(key, scope.encode(), hashlib.sha256).hexdigest()


def is_worker(presented: Optional[str], scope: str) -> bool:
    expected = token_for(scope)
    return bool(expected and presented and hmac.compare_digest(presented, expected))


def require_worker(presented: Optional[str], scope: str) -> None:
    """Raise unless `presented` is the worker token for `scope`."""
    if _worker_key() is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Worker authentication is not configured (set LIVEKIT_API_SECRET or WORKER_SERVICE_TOKEN)",
        )
    if not is_worker(presented, scope):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Worker credentials required")


def worker_token_header(x_worker_token: Optional[str] = Header(default=None, alias=HEADER)) -> Optional[str]:
    """FastAPI dependency: the raw header, verified by the route against its scope."""
    return x_worker_token
