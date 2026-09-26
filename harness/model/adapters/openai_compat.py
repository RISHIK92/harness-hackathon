"""OpenAI-shaped adapter (FR-10).

Covers OpenAI, OpenRouter, Groq, xAI, Cerebras, DeepSeek, Mistral, Together,
Fireworks, Google's compat endpoint, Ollama, vLLM, LM Studio, and anything
else reachable via AI_BASE_URL.
"""
from __future__ import annotations

import time

from ..http import request
from ..types import ModelReply, ToolCall

WIRE = "openai"


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def list_models(base_url: str, api_key: str, path: str = "/models",
                timeout: float = 2.5) -> list[dict]:
    out = request("GET", base_url.rstrip("/") + path, _headers(api_key),
                  timeout=timeout)
    items = out.get("data") if isinstance(out, dict) else None
    if items is None and isinstance(out, dict):
        items = out.get("models", [])
    return [m for m in (items or []) if isinstance(m, dict)]


def model_ids(items: list[dict]) -> list[str]:
    ids = []
    for m in items:
        mid = m.get("id") or m.get("name") or m.get("model")
        if mid:
            ids.append(str(mid))
    return ids


def context_window(item: dict) -> int | None:
    for key in ("context_length", "context_window", "max_context_length",
                "max_model_len"):
        val = item.get(key)
        if isinstance(val, int) and val > 0:
            return val
    top = item.get("top_provider")
    if isinstance(top, dict) and isinstance(top.get("context_length"), int):
        return top["context_length"]
    return None


def chat(base_url: str, api_key: str, model: str, messages: list[dict],
         max_tokens: int = 4096, temperature: float = 0.0,
         tools: list[dict] | None = None, timeout: float = 120.0,
         **_ignored) -> ModelReply:
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        body["tools"] = [{"type": "function", "function": t} for t in tools]

    started = time.time()
    out = request("POST", base_url.rstrip("/") + "/chat/completions",
                  _headers(api_key), body, timeout=timeout)
    latency = int((time.time() - started) * 1000)

    choices = out.get("choices") or [{}]
    msg = (choices[0] or {}).get("message", {}) or {}
    usage = out.get("usage", {}) or {}
    details = usage.get("prompt_tokens_details", {}) or {}

    calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {}) or {}
        calls.append(ToolCall(name=fn.get("name", ""),
                              args=_parse_args(fn.get("arguments")),
                              id=tc.get("id", "")))

    return ModelReply(
        text=msg.get("content") or "",
        tool_calls=calls,
        tokens_in=int(usage.get("prompt_tokens") or 0),
        tokens_out=int(usage.get("completion_tokens") or 0),
        tokens_cached=int(details.get("cached_tokens") or 0),
        stop_reason=(choices[0] or {}).get("finish_reason", "") or "",
        model=out.get("model", model),
        latency_ms=latency,
        raw=out,
    )


def _parse_args(raw) -> dict:
    import json
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {"_": val}
    except (json.JSONDecodeError, TypeError):
        return {"_raw": str(raw)}
