"""Model ranking, tier inference and selection (FR-8, FR-9, SPEC.md 3.3).

Two failures this module exists to prevent:
  1. selecting a non-chat model (an unfiltered /v1/models listing contains
     embeddings, TTS and image models -- `dall-e-3` as primary is a dead run);
  2. selecting "whatever the API returned first", which is non-deterministic
     across runs and violates NFR-5.
"""
from __future__ import annotations

import re

# Substrings that mark a model as not usable for chat completion.
NON_CHAT = (
    "embed", "whisper", "tts", "dall-e", "moderation", "rerank", "clip",
    "stable-diffusion", "sd3", "flux", "image", "audio", "speech", "guard",
    "safety", "bge-", "nomic", "voice", "transcribe", "realtime", "search-",
    "codestral-embed", "upscal",
)

# Per-provider ranking, best first: (id_substring, tier).
MODEL_RANKING: dict[str, list[tuple[str, str]]] = {
    "anthropic": [("opus", "T2"), ("sonnet", "T1"), ("haiku", "T0")],
    "openai": [
        ("gpt-5", "T2"), ("o3", "T2"), ("o1", "T2"),
        ("gpt-4.1", "T1"), ("gpt-4o", "T1"), ("o4-mini", "T1"),
        ("gpt-5-mini", "T0"), ("gpt-4.1-mini", "T0"), ("gpt-4o-mini", "T0"),
        ("gpt-4.1-nano", "T0"), ("gpt-5-nano", "T0"),
        ("mini", "T0"), ("nano", "T0"), ("gpt-3.5", "T0"),
    ],
    "google": [("pro", "T2"), ("flash-lite", "T0"), ("flash", "T1")],
    "groq": [("70b", "T1"), ("32b", "T1"), ("8b", "T0"), ("instant", "T0")],
    "cerebras": [("70b", "T1"), ("8b", "T0")],
    "xai": [("grok-4", "T2"), ("grok-3", "T1"), ("mini", "T0")],
    "deepseek": [("reasoner", "T2"), ("chat", "T1")],
    "mistral": [("large", "T2"), ("medium", "T1"), ("small", "T0")],
    "openrouter": [],       # heterogeneous catalogue: heuristics only
}

# Whole-token hints. Substring matching is wrong here: "gemini" contains
# "mini", which would make every Gemini model T0.
TIER_HINTS = {
    "T2": {"opus", "pro", "ultra", "max", "thinking", "reasoner", "r1"},
    "T0": {"mini", "nano", "small", "haiku", "tiny", "lite", "instant",
           "turbo"},
}
T2_SUBSTRINGS = ("gpt-5", "o3-", "o1-", "-405b", "claude-opus")
_SIZE = re.compile(r"^(\d+(?:\.\d+)?)b$")

TIER_ORDER = {"T2": 0, "T1": 1, "T0": 2}

# Context-window hints used when the provider does not report one.
CTX_HINTS = (
    ("claude", 200_000), ("gpt-5", 400_000), ("gpt-4.1", 1_000_000),
    ("gpt-4o", 128_000), ("gemini", 1_000_000), ("llama-3.3", 128_000),
    ("llama-3.1", 128_000), ("grok", 131_072), ("deepseek", 65_536),
    ("mistral", 32_000), ("qwen", 32_768),
)


class NoChatModel(Exception):
    """The key exposes no chat-capable model. Maps to exit 4."""


def chat_capable(model_id: str) -> bool:
    m = (model_id or "").lower()
    if not m:
        return False
    return not any(tok in m for tok in NON_CHAT)


def _tokens(model_id: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9.]+", (model_id or "").lower()) if t}


def _table_match(model_id: str, provider: str) -> tuple[int, str] | None:
    """Longest matching table entry -> (index, tier).

    Longest wins so that "gpt-4o-mini" resolves to its own entry rather than
    to "gpt-4o"; the index is the entry's rank position.
    """
    m = (model_id or "").lower()
    best: tuple[int, str] | None = None
    best_len = -1
    for i, (pattern, tier) in enumerate(MODEL_RANKING.get(provider, [])):
        if pattern in m and len(pattern) > best_len:
            best, best_len = (i, tier), len(pattern)
    return best


def tier_of(model_id: str, provider: str = "") -> str:
    m = (model_id or "").lower()
    hit = _table_match(model_id, provider)
    if hit:
        return hit[1]

    toks = _tokens(m)
    if toks & TIER_HINTS["T2"] or any(s in m for s in T2_SUBSTRINGS):
        return "T2"
    for t in toks:
        size = _SIZE.match(t)
        if size and float(size.group(1)) >= 200:
            return "T2"
    if toks & TIER_HINTS["T0"]:
        return "T0"
    for t in toks:
        size = _SIZE.match(t)
        if size and float(size.group(1)) <= 9:
            return "T0"
    return "T1"          # unknown ids default to T1 with conservative caps


def ctx_of(model_id: str, reported: int | None = None) -> int:
    if reported:
        return int(reported)
    m = (model_id or "").lower()
    for pattern, ctx in CTX_HINTS:
        if pattern in m:
            return ctx
    return 32_000        # conservative floor


def sort_key(model_id: str, provider: str) -> tuple:
    """Total order: table position (longest match) first, then tier, then id."""
    hit = _table_match(model_id, provider)
    if hit:
        return (0, hit[0], model_id)
    return (1, TIER_ORDER[tier_of(model_id, provider)], model_id)


def rank_models(available: list[str], provider: str) -> list[str]:
    """Chat-capable models, best first, under a deterministic total order."""
    chat = [m for m in available if chat_capable(m)]
    return sorted(chat, key=lambda m: sort_key(m, provider))


def select_models(available: list[str], provider: str,
                  model_override: str | None = None,
                  cheap_override: str | None = None) -> tuple[str, str]:
    """FR-8: best for primary phases, cheapest for verification.

    FR-9: overrides win outright and are NOT validated against the listing --
    a proxy may serve ids it does not advertise.
    """
    ordered = rank_models(available, provider)

    if model_override and cheap_override:
        return model_override, cheap_override
    if not ordered:
        if model_override:
            return model_override, cheap_override or model_override
        raise NoChatModel(
            "no chat-capable model in the key's model list "
            f"({len(available)} listed)"
        )

    primary = model_override or ordered[0]
    cheap = cheap_override or ordered[-1]
    return primary, cheap
