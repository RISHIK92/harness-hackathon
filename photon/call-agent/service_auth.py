"""The worker's credential for the brain-api.

Mirrors server/app/core/service_auth.py — the two derivations must agree:

    key          = WORKER_SERVICE_TOKEN, else HMAC-SHA256(LIVEKIT_API_SECRET, "photon-worker-v1")
    token(scope) = HMAC-SHA256(key, scope)          scope = "meeting:<slug>" | "worker:heartbeat"

A token is scoped to one meeting, so one captured from a call opens nothing
else, and the worker derives it from the room name it was dispatched to —
nothing to provision per meeting. LIVEKIT_API_SECRET is already in this
worker's environment (it registers with LiveKit using it), so a default
deployment needs no new configuration.
"""
from __future__ import annotations

import hashlib
import hmac
import os

HEADER = "X-Worker-Token"
HEARTBEAT_SCOPE = "worker:heartbeat"


def _key() -> bytes | None:
    explicit = os.environ.get("WORKER_SERVICE_TOKEN", "")
    if explicit:
        return explicit.encode()
    secret = os.environ.get("LIVEKIT_API_SECRET", "")
    if secret:
        return hmac.new(secret.encode(), b"photon-worker-v1", hashlib.sha256).digest()
    return None


def meeting_scope(slug: str) -> str:
    """Same normalisation as server/app/services/meeting_slug.normalise —
    the room name is the slug, but a scope must match it byte for byte."""
    cleaned = "".join(c for c in (slug or "").lower() if c.isalnum())
    if len(cleaned) != 8:
        return f"meeting:{(slug or '').strip().lower()}"
    return f"meeting:{cleaned[:4]}-{cleaned[4:]}"


def token_for(scope: str) -> str | None:
    key = _key()
    return hmac.new(key, scope.encode(), hashlib.sha256).hexdigest() if key else None


def headers_for(scope: str) -> dict[str, str]:
    token = token_for(scope)
    return {HEADER: token} if token else {}
