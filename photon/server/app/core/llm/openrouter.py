"""OpenRouter chat-completions client (OpenAI-compatible). This is the text-
generation AND vision provider for the agent loop, check_conflict, screen-
frame analysis, and the web console's /api/query streaming — see
app.config.Settings for why Gemini's direct API was dropped for both (its
free tier is capped at 20 requests/DAY, nowhere near enough — confirmed to
hit the same wall for the vision model too, not just text, when screen-
share analysis was first wired up). `google/gemini-3.7-flash` is still the
model in use for vision, just routed through OpenRouter's billing instead
of a Gemini API key directly — same model, no quota trap.

Deliberately provider-specific rather than hidden behind an abstraction —
there's exactly one text/vision provider in use, and an interface for a
hypothetical second one would be unused generality.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import get_settings

log = structlog.get_logger()
settings = get_settings()

_URL = "https://openrouter.ai/api/v1/chat/completions"

# One pooled client for every call instead of httpx.post()'s implicit
# connect-per-call. Measured: the same vision call took ~1.1s on a warm
# pooled connection in the bench but ~2.0s through the agent, and the
# difference is a fresh TCP + TLS handshake to OpenRouter every single
# time. A turn makes 2-3 of these calls, so the handshake was being paid
# 2-3 times per question. httpx.Client is thread-safe for requests, which
# matters because these run in a thread executor.
_client = httpx.Client(
    timeout=60.0,
    limits=httpx.Limits(max_keepalive_connections=8, keepalive_expiry=300.0),
)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503)
    # Observed directly: some calls to this model via OpenRouter take 60s+
    # with no error, just a slow upstream response — worth one retry rather
    # than failing the whole agent turn outright, though a demo-time fix
    # (dedicated tier / different model) is the real answer if this proves
    # frequent. See CLAUDE.md.
    return isinstance(exc, (httpx.ReadTimeout, httpx.ConnectTimeout))


@retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(2),
    reraise=True,
)
def sync_chat(
    prompt: str,
    max_tokens: int = 1500,
    temperature: float = 0.1,
    json_mode: bool = False,
    json_schema: dict | None = None,
) -> str:
    """Blocking call — run in a thread executor from async code.

    json_schema constrains DECODING to a grammar, where json_mode only asks
    the model to please emit JSON. That difference is the whole point: with
    json_mode this model reliably produced almost-valid output with a stray
    token welded on — `…}]5}`, `…}] K}`, `…}]absolut}` — which is not a
    parsing problem to work around but a generation problem to remove.

    Note `strict: False`. Strict mode forbids a free-form object, and the
    planner's `args` is exactly that (every tool takes different arguments),
    so a strict schema emitted `"args": {}` every time — syntactically perfect
    and semantically empty, which is worse than malformed. Verified against
    the live endpoint before choosing this shape.
    """
    body = {
        "model": settings.openrouter_chat_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_schema:
        body["response_format"] = {"type": "json_schema", "json_schema": json_schema}
    elif json_mode:
        body["response_format"] = {"type": "json_object"}

    response = _client.post(_URL, headers=_headers(), json=body)
    response.raise_for_status()
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        log.warning("openrouter.no_choices", data=data)
        return ""
    return (choices[0].get("message", {}).get("content") or "").strip()


@retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(2),
    reraise=True,
)
def sync_chat_vision(prompt: str, image_bytes: bytes, max_tokens: int = 300, temperature: float = 0.1) -> str:
    """Blocking call with one image attached (OpenAI-compatible image_url
    content part, base64 data URI). Run in a thread executor from async code."""
    import base64

    b64 = base64.b64encode(image_bytes).decode()
    body = {
        "model": settings.openrouter_vision_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    response = _client.post(_URL, headers=_headers(), json=body)
    response.raise_for_status()
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        log.warning("openrouter.vision_no_choices", data=data)
        return ""
    return (choices[0].get("message", {}).get("content") or "").strip()


async def stream_chat(prompt: str, max_tokens: int = 2048, temperature: float = 0.2) -> AsyncIterator[str]:
    """Async generator of text deltas via OpenRouter's SSE streaming."""
    body = {
        "model": settings.openrouter_chat_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", _URL, headers=_headers(), json=body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[len("data: ") :].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                text = delta.get("content")
                if text:
                    yield text
