"""Model discovery (FR-7) with the chat-capability filter (SPEC.md 3.3)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .adapters import anthropic as anthropic_adapter
from .adapters import openai_compat
from .detect import Provider
from .rank import chat_capable, ctx_of
from .types import ProviderError

ADAPTERS = {"openai": openai_compat, "anthropic": anthropic_adapter}


@dataclass
class Discovery:
    ids: list[str] = field(default_factory=list)
    raw_count: int = 0
    ctx: dict = field(default_factory=dict)
    price: dict = field(default_factory=dict)      # id -> USD per input token
    ok: bool = False
    error: str = ""

    @property
    def degraded(self) -> bool:
        return not self.ok


def adapter_for(provider: Provider):
    return ADAPTERS.get(provider.wire, openai_compat)


def discover(provider: Provider, api_key: str, cache_dir: Path | None = None,
             timeout: float = 2.5, use_cache: bool = True) -> Discovery:
    """One GET, one retry, then degrade. Never raises."""
    cache_file = None
    if cache_dir and use_cache:
        key = f"{provider.name}-{provider.base_url}".replace("/", "_")
        cache_file = Path(cache_dir) / f"models-{abs(hash(key)) % 10**10}.json"
        if cache_file.is_file():
            age = time.time() - cache_file.stat().st_mtime
            if age < 3600:
                try:
                    d = json.loads(cache_file.read_text("utf-8"))
                    return Discovery(ids=d["ids"], raw_count=d["raw_count"],
                                     ctx=d.get("ctx", {}), ok=True)
                except (OSError, json.JSONDecodeError, KeyError):
                    pass

    adapter = adapter_for(provider)
    last_err = ""
    for attempt in range(2):
        try:
            items = adapter.list_models(provider.base_url, api_key,
                                        provider.models_path, timeout=timeout)
            all_ids = adapter.model_ids(items)
            ids = [m for m in all_ids if chat_capable(m)]
            ctx, price = {}, {}
            getter = getattr(adapter, "input_price", None)
            for item in items:
                mid = item.get("id") or item.get("name") or ""
                if mid in ids:
                    ctx[mid] = ctx_of(mid, adapter.context_window(item))
                    if getter is not None:
                        found = getter(item)
                        if found is not None:
                            price[mid] = found
            result = Discovery(ids=ids, raw_count=len(all_ids), ctx=ctx,
                               price=price, ok=True)
            if cache_file:
                try:
                    cache_file.parent.mkdir(parents=True, exist_ok=True)
                    cache_file.write_text(json.dumps({
                        "ids": ids, "raw_count": len(all_ids), "ctx": ctx,
                    }), encoding="utf-8")
                except OSError:
                    pass
            return result
        except ProviderError as exc:
            last_err = str(exc)
            if attempt == 0:
                time.sleep(0.5)

    return Discovery(ok=False, error=last_err)


def probe_host(provider: Provider, api_key: str, timeout: float = 1.5) -> bool:
    """Cheap liveness check used by the bare-`sk-` ambiguity ladder."""
    try:
        adapter_for(provider).list_models(provider.base_url, api_key,
                                          provider.models_path, timeout=timeout)
        return True
    except ProviderError:
        return False
