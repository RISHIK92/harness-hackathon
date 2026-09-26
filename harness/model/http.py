"""Minimal HTTP on the stdlib (SPEC.md 2.3).

No provider SDKs: zero install risk, no version drift, and the harness never
streams -- the one thing an SDK would buy.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .types import ProviderError

USER_AGENT = "ai-coding-harness/2.1"


def request(method: str, url: str, headers: dict, body: dict | None = None,
            timeout: float = 60.0) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = {"User-Agent": USER_AGENT, **headers}
    if data is not None:
        hdrs["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
        except (OSError, ValueError, AttributeError):
            detail = "<error body unavailable>"
        retry_after = None
        try:
            ra = exc.headers.get("Retry-After") if exc.headers else None
            retry_after = float(ra) if ra else None
        except (TypeError, ValueError):
            retry_after = None
        raise ProviderError(f"HTTP {exc.code}: {detail}", status=exc.code,
                            retry_after=retry_after) from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"transport: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise ProviderError(f"transport: {exc}") from exc

    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"malformed JSON from provider: {raw[:200]}") from exc
