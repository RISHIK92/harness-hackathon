"""OpenAI-shaped adapter (FR-10).

Covers OpenAI, OpenRouter, Groq, xAI, Cerebras, DeepSeek, Mistral, Together,
Fireworks, Google's compat endpoint, Ollama, vLLM, LM Studio, and anything
else reachable via AI_BASE_URL.
"""
from __future__ import annotations

import re

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


def input_price(item: dict) -> float | None:
    """USD per input token, when the listing says. None when it does not.

    Hardcoded lists of "cheap models" rot: every name in one is a guess
    about a catalogue that changes weekly. The listing states the price, so
    use it and keep the guessing for providers that say nothing.
    """
    pricing = item.get("pricing")
    if not isinstance(pricing, dict):
        return None
    for key in ("prompt", "input", "input_tokens"):
        raw = pricing.get(key)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


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
        text=visible_text(msg),
        tool_calls=calls,
        tokens_in=int(usage.get("prompt_tokens") or 0),
        tokens_out=int(usage.get("completion_tokens") or 0),
        tokens_cached=int(details.get("cached_tokens") or 0),
        stop_reason=(choices[0] or {}).get("finish_reason", "") or "",
        model=out.get("model", model),
        latency_ms=latency,
        raw=out,
    )


# Reasoning models -- DeepSeek R1 / deepseek-reasoner, Qwen QwQ and the
# Qwen3 thinking variants -- put their working out in the reply. Two shapes
# occur, and both break every parser downstream if passed through:
#
#   * inline `<think> ... </think>` ahead of the answer (most gateways,
#     including OpenRouter), which makes a JSON reply un-parseable and puts
#     prose in front of an edit block;
#   * a separate `reasoning_content` field with `content` holding the answer
#     (DeepSeek's own API), which is harmless but must not be concatenated.
#
# Stripping is deliberately done here, at the wire, so no phase has to know
# which model it is talking to.
THINK_BLOCK = re.compile(
    r"<(think|thinking|reason|reasoning)\b[^>]*>.*?</\1\s*>",
    re.S | re.I)
# An unterminated opener happens when the reply is cut off at max_tokens.
THINK_OPEN = re.compile(r"<(think|thinking|reason|reasoning)\b[^>]*>.*\Z",
                        re.S | re.I)


def strip_reasoning(text: str) -> str:
    if not text or "<" not in text:
        return text or ""
    cleaned = THINK_BLOCK.sub("", text)
    cleaned = THINK_OPEN.sub("", cleaned)
    return cleaned.strip()


def visible_text(msg: dict) -> str:
    """The answer, with any reasoning removed.

    When a model spends its whole budget reasoning and returns no answer,
    the reply is genuinely empty -- reporting that is right, because the
    caller then retries or degrades instead of parsing a monologue.
    """
    return strip_reasoning(msg.get("content") or "")


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
