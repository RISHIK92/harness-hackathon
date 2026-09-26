"""Anthropic Messages API adapter, including ephemeral prompt caching."""
from __future__ import annotations

import time

from ..http import request
from ..types import ModelReply, ToolCall

WIRE = "anthropic"
VERSION = "2023-06-01"


def _headers(api_key: str) -> dict:
    return {"x-api-key": api_key, "anthropic-version": VERSION}


def list_models(base_url: str, api_key: str, path: str = "/v1/models",
                timeout: float = 2.5) -> list[dict]:
    out = request("GET", base_url.rstrip("/") + path, _headers(api_key),
                  timeout=timeout)
    return [m for m in (out.get("data") or []) if isinstance(m, dict)]


def model_ids(items: list[dict]) -> list[str]:
    return [str(m["id"]) for m in items if m.get("id")]


def context_window(item: dict) -> int | None:
    for key in ("context_window", "context_length", "max_context_length"):
        val = item.get(key)
        if isinstance(val, int) and val > 0:
            return val
    return None


def chat(base_url: str, api_key: str, model: str, messages: list[dict],
         max_tokens: int = 4096, temperature: float = 0.0,
         tools: list[dict] | None = None, timeout: float = 120.0,
         cache_prefix: int = 0, **_ignored) -> ModelReply:
    """`cache_prefix` = number of leading system blocks to mark cacheable."""
    system_blocks, convo = [], []
    for m in messages:
        if m.get("role") == "system":
            system_blocks.append({"type": "text", "text": m.get("content", "")})
        else:
            convo.append({"role": m["role"], "content": m.get("content", "")})

    if cache_prefix and system_blocks:
        idx = min(cache_prefix, len(system_blocks)) - 1
        system_blocks[idx]["cache_control"] = {"type": "ephemeral"}

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": convo or [{"role": "user", "content": ""}],
    }
    if system_blocks:
        body["system"] = system_blocks
    if tools:
        body["tools"] = [
            {"name": t["name"], "description": t.get("description", ""),
             "input_schema": t.get("parameters", {"type": "object"})}
            for t in tools
        ]

    started = time.time()
    out = request("POST", base_url.rstrip("/") + "/v1/messages",
                  _headers(api_key), body, timeout=timeout)
    latency = int((time.time() - started) * 1000)

    text, calls = "", []
    for block in out.get("content") or []:
        if block.get("type") == "text":
            text += block.get("text", "")
        elif block.get("type") == "tool_use":
            calls.append(ToolCall(name=block.get("name", ""),
                                  args=block.get("input", {}) or {},
                                  id=block.get("id", "")))

    usage = out.get("usage", {}) or {}
    return ModelReply(
        text=text,
        tool_calls=calls,
        tokens_in=int(usage.get("input_tokens") or 0),
        tokens_out=int(usage.get("output_tokens") or 0),
        tokens_cached=int(usage.get("cache_read_input_tokens") or 0),
        stop_reason=out.get("stop_reason", "") or "",
        model=out.get("model", model),
        latency_ms=latency,
        raw=out,
    )
