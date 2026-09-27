"""The single door to every provider (SPEC.md 3.5).

Retries with backoff, normalizes replies, charges the budget, caches calls,
traces to the event log, and fails over primary -> cheap rather than aborting.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path

from .discover import adapter_for
from .detect import Provider
from .types import ModelReply, ProviderError

MAX_ATTEMPTS = 4


@dataclass
class ModelSpec:
    id: str
    tier: str
    ctx: int


class Gateway:
    def __init__(self, provider: Provider, api_key: str, cfg, log,
                 events=None, budgets=None) -> None:
        self.provider = provider
        self.api_key = api_key
        self.cfg = cfg
        self.log = log
        self.events = events
        self.budgets = budgets
        self.adapter = adapter_for(provider)
        self.cache_dir = cfg.work_dir / "cache" / "calls"
        self.primary: ModelSpec | None = None
        self.cheap: ModelSpec | None = None
        self.failed_primary = 0
        self.calls = 0

    # -- caching -----------------------------------------------------------
    def _cache_key(self, model: str, messages: list[dict], params: dict) -> str:
        blob = json.dumps([model, messages, params], sort_keys=True,
                          default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def _cache_get(self, key: str) -> ModelReply | None:
        if self.cfg.no_cache:
            return None
        f = self.cache_dir / f"{key}.json"
        if not f.is_file():
            return None
        try:
            d = json.loads(f.read_text("utf-8"))
            return ModelReply(**d)
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def _cache_put(self, key: str, reply: ModelReply) -> None:
        if self.cfg.no_cache:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            d = reply.__dict__.copy()
            d["tool_calls"] = [tc.__dict__ for tc in reply.tool_calls]
            d["raw"] = {}
            (self.cache_dir / f"{key}.json").write_text(
                json.dumps(d, default=str), encoding="utf-8")
        except OSError:
            pass

    # -- the call ----------------------------------------------------------
    def call(self, messages: list[dict], model: str, phase: str = "P0",
             max_tokens: int = 4096, temperature: float = 0.0,
             tools: list[dict] | None = None, cache_prefix: int = 0,
             label: str = "", no_cache: bool = False) -> ModelReply:
        params = {"max_tokens": max_tokens, "temperature": temperature,
                  "tools": tools or []}
        key = self._cache_key(model, messages, params)

        # A retry after a truncated reply must reach the provider. Serving
        # the cached failure again cannot produce a different answer -- that
        # is how four cycles produced four identical empty replies.
        cached = None if no_cache else self._cache_get(key)
        if cached is not None:
            self.calls += 1
            self.log.model_call(model, cached.tokens_in, cached.tokens_out,
                                0.0, extra=label or "call",
                                from_cache=True)
            return cached

        if self.events:
            self.events.append("model_request", phase,
                               {"model": model, "messages": messages,
                                "params": params, "label": label},
                               summary=label or f"call {model}")

        reply = self._attempt(messages, model, phase, max_tokens, temperature,
                              tools, cache_prefix)

        self.calls += 1
        if self.budgets:
            self.budgets.tokens.charge(phase, reply.tokens_in, reply.tokens_out,
                                       reply.tokens_cached)
        self.log.model_call(reply.model or model, reply.tokens_in,
                            reply.tokens_out, reply.latency_ms / 1000.0,
                            cached=reply.tokens_cached, extra=label)
        if self.events:
            self.events.append("model_reply", phase,
                               {"model": reply.model, "text": reply.text,
                                "tool_calls": [tc.__dict__ for tc in reply.tool_calls],
                                "usage": {"in": reply.tokens_in,
                                          "out": reply.tokens_out,
                                          "cached": reply.tokens_cached}},
                               summary=f"{reply.stop_reason or 'ok'}",
                               tokens_est=reply.tokens_in + reply.tokens_out)
        self._cache_put(key, reply)
        return reply

    def _attempt(self, messages, model, phase, max_tokens, temperature, tools,
                 cache_prefix) -> ModelReply:
        last: ProviderError | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                return self.adapter.chat(
                    self.provider.base_url, self.api_key, model, messages,
                    max_tokens=max_tokens, temperature=temperature,
                    tools=tools, cache_prefix=cache_prefix)
            except ProviderError as exc:
                last = exc
                if not exc.retryable or attempt == MAX_ATTEMPTS - 1:
                    break
                delay = exc.retry_after or (0.5 * (2 ** attempt))
                delay += random.uniform(0, 0.25)
                self.log.debug(f"retry {attempt+1}/{MAX_ATTEMPTS} in {delay:.1f}s"
                               f" ({exc})")
                time.sleep(min(delay, 8.0))

        # Failover: primary -> cheap rather than aborting the run (NFR-2).
        if (self.primary and model == self.primary.id and self.cheap
                and self.cheap.id != model):
            self.failed_primary += 1
            if self.failed_primary >= 1:
                self.log.degraded("provider_failover",
                                  f"{model} -> {self.cheap.id}")
                if self.events:
                    self.events.append("degradation", phase,
                                       {"tag": "provider_failover",
                                        "from": model, "to": self.cheap.id})
                return self._attempt(messages, self.cheap.id, phase, max_tokens,
                                     temperature, tools, cache_prefix)
        raise last or ProviderError("model call failed")
