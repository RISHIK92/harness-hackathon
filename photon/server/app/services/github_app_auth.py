"""Short-lived tokens for GitHub App installations, and the App's own
RS256 JWT used to mint them.

Two-step auth, same as GitHub's own docs describe: (1) sign a JWT with the
App's private key, identifying the App itself (`iss`), (2) use that JWT to
ask GitHub for a scoped, ~1hr installation access token — the thing that
actually reads repos. Never use the App JWT itself for API calls other
than minting installation tokens.

Sync core (called directly from the Celery ingestion task, which is sync)
with a thin async wrapper for FastAPI routes — same split as
app/core/llm/openrouter.py's sync_chat + async callers.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import redis
import structlog
from jose import jwt

from app.config import get_settings

log = structlog.get_logger()
settings = get_settings()

# GitHub installation tokens last 60 min; refresh 5 min early so a
# long-running clone never has one expire mid-request.
_INSTALL_TOKEN_TTL_SECONDS = 55 * 60
# GitHub caps the App JWT's lifetime at 10 min; stay comfortably under.
_APP_JWT_TTL_SECONDS = 9 * 60

_redis_client: "redis.Redis | None" = None


def _redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.redis_url)
    return _redis_client


def generate_app_jwt() -> str:
    if not settings.github_app_id or not settings.github_app_private_key:
        raise RuntimeError("GitHub App is not configured on this deployment (run the manifest bootstrap flow first)")
    now = int(time.time())
    payload = {
        "iat": now - 30,  # backdated slightly for clock drift, per GitHub's own guidance
        "exp": now + _APP_JWT_TTL_SECONDS,
        "iss": settings.github_app_id,
    }
    return jwt.encode(payload, settings.github_app_private_key, algorithm="RS256")


def get_installation_token(installation_id: int) -> str:
    """Blocking. Safe to call directly from the (sync) Celery ingestion
    task; async callers should use get_installation_token_async."""
    cache_key = f"gh:install_token:{installation_id}"
    cached = _redis().get(cache_key)
    if cached:
        log.info("github_app.installation_token_cache_hit", installation_id=installation_id)
        return cached.decode() if isinstance(cached, bytes) else cached

    app_jwt = generate_app_jwt()
    response = httpx.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        headers={"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json"},
        timeout=15.0,
    )
    response.raise_for_status()
    token = response.json()["token"]
    _redis().set(cache_key, token, ex=_INSTALL_TOKEN_TTL_SECONDS)
    # Never log the token or the app_jwt — installation_id is the only
    # identifying detail that belongs in a log line here.
    log.info("github_app.installation_token_minted", installation_id=installation_id)
    return token


async def get_installation_token_async(installation_id: int) -> str:
    return await asyncio.get_event_loop().run_in_executor(None, get_installation_token, installation_id)
