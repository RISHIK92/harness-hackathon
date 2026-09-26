"""Provider detection from the key prefix -- no API call (FR-6, SPEC.md 3.2).

Matching is LONGEST PREFIX FIRST.  A naive table that maps "sk-" to OpenAI
routes OpenRouter (sk-or-v1-) and project (sk-proj-) keys to the wrong host,
which 401s: the single most common BYOK bug.
"""
from __future__ import annotations

from dataclasses import dataclass

AMBIGUOUS = "AMBIGUOUS"

# (prefix, provider) -- order here is documentation; matching sorts by length.
PROVIDER_PREFIXES: list[tuple[str, str]] = [
    ("sk-ant-api", "anthropic"),
    ("sk-ant-", "anthropic"),
    ("sk-or-v1-", "openrouter"),
    ("sk-svcacct-", "openai"),
    ("sk-admin-", "openai"),
    ("sk-proj-", "openai"),
    ("gsk_", "groq"),
    ("xai-", "xai"),
    ("csk-", "cerebras"),
    ("AIza", "google"),
    ("tgp_v1_", "together"),
    ("fw_", "fireworks"),
    ("sk-", AMBIGUOUS),
]

_SORTED = sorted(PROVIDER_PREFIXES, key=lambda p: -len(p[0]))


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    wire: str           # "openai" | "anthropic" | "google"
    models_path: str


PROVIDERS: dict[str, Provider] = {
    "anthropic": Provider("anthropic", "https://api.anthropic.com",
                          "anthropic", "/v1/models"),
    "openrouter": Provider("openrouter", "https://openrouter.ai/api/v1",
                           "openai", "/models"),
    "openai": Provider("openai", "https://api.openai.com/v1",
                       "openai", "/models"),
    "groq": Provider("groq", "https://api.groq.com/openai/v1",
                     "openai", "/models"),
    "xai": Provider("xai", "https://api.x.ai/v1", "openai", "/models"),
    "cerebras": Provider("cerebras", "https://api.cerebras.ai/v1",
                         "openai", "/models"),
    "google": Provider("google",
                       "https://generativelanguage.googleapis.com/v1beta/openai",
                       "openai", "/models"),
    "together": Provider("together", "https://api.together.xyz/v1",
                         "openai", "/models"),
    "fireworks": Provider("fireworks", "https://api.fireworks.ai/inference/v1",
                          "openai", "/models"),
    "deepseek": Provider("deepseek", "https://api.deepseek.com/v1",
                         "openai", "/models"),
    "mistral": Provider("mistral", "https://api.mistral.ai/v1",
                        "openai", "/models"),
    "openai_compatible": Provider("openai_compatible", "",
                                  "openai", "/models"),
}

# Fixed probe order for a bare "sk-" key (SPEC.md 3.2 ambiguity ladder).
AMBIGUOUS_CANDIDATES = ("openai", "deepseek", "mistral")


def detect_provider(api_key: str) -> str:
    """Prefix-only. Returns a provider name, or AMBIGUOUS, or the fallback."""
    key = (api_key or "").strip()
    if not key:
        return "openai_compatible"
    for prefix, provider in _SORTED:
        if key.startswith(prefix):
            return provider
    return "openai_compatible"


def resolve(api_key: str, base_url: str | None = None,
            forced: str | None = None, prober=None) -> Provider:
    """Full resolution: overrides, then prefix, then the ambiguity ladder.

    `prober(Provider) -> bool` performs a cheap GET on a candidate host.  It is
    model *discovery*, not detection, so FR-6's "no API call" still holds.

    FR-10 note: AI_BASE_URL means "an OpenAI-compatible provider", so setting
    it selects the OpenAI wire even when the key's prefix names another
    vendor -- an Anthropic key in front of an OpenAI-compatible gateway is the
    common case.  HARNESS_PROVIDER overrides this to get a different wire.
    """
    if forced:
        p = PROVIDERS.get(forced) or PROVIDERS["openai_compatible"]
        return _with_base(p, base_url)

    if base_url:
        return _with_base(PROVIDERS["openai_compatible"], base_url)

    name = detect_provider(api_key)

    if name == AMBIGUOUS:
        if prober:
            for cand in AMBIGUOUS_CANDIDATES:
                if prober(PROVIDERS[cand]):
                    return PROVIDERS[cand]
        return PROVIDERS["openai"]          # documented default

    provider = PROVIDERS[name]
    if name == "openai_compatible" and not base_url:
        # Unknown shape with no endpoint: caller turns this into exit 4.
        return provider
    return _with_base(provider, base_url)


def _with_base(p: Provider, base_url: str | None) -> Provider:
    if not base_url:
        return p
    return Provider(p.name, base_url.rstrip("/"), p.wire, p.models_path)
