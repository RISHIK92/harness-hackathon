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
    # DeepSeek and Qwen, as the catalogues actually name them today. Both
    # families moved on from the ids these tables were written against:
    # "deepseek-chat" is now v3.x, and Qwen 3 puts its capability in words
    # like coder/max/next rather than in a size suffix. Longest match wins,
    # so the specific entries above the generic ones are what decide.
    "deepseek": [
        ("v4-pro", "T2"), ("pro-latest", "T2"),
        ("reasoner", "T2"), ("r1", "T2"),
        ("v4.1-flash", "T1"), ("v4-flash", "T1"), ("flash", "T1"),
        ("v3.2", "T1"), ("v3.1", "T1"), ("chat", "T1"), ("coder", "T1"),
        # A distill is a small model wearing a big model's name.
        ("distill", "T0"),
    ],
    "qwen": [
        ("max-thinking", "T2"), ("qwen3-max", "T2"), ("max", "T2"),
        ("coder-plus", "T2"), ("coder-next", "T2"),
        ("235b", "T2"), ("480b", "T2"),
        ("coder-flash", "T1"), ("coder", "T1"), ("plus", "T1"),
        ("next-80b", "T1"), ("32b", "T1"), ("30b", "T1"), ("14b", "T1"),
        ("turbo", "T0"), ("flash", "T0"), ("8b", "T0"), ("7b", "T0"),
        ("4b", "T0"), ("1.5b", "T0"),
    ],
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

# The cheap model judges diffs and summarises: it needs room for the diff
# and the prompt, whatever it costs.
CHEAP_MIN_CTX = 64_000

TIER_ORDER = {"T2": 0, "T1": 1, "T0": 2}

# Context-window hints used when the provider does not report one.
CTX_HINTS = (
    ("claude", 200_000), ("gpt-5", 400_000), ("gpt-4.1", 1_000_000),
    ("gpt-4o", 128_000), ("gemini", 1_000_000), ("llama-3.3", 128_000),
    ("llama-3.1", 128_000), ("grok", 131_072),
    # Measured from the provider catalogue, not remembered: the old
    # deepseek=65k / qwen=32k floors under-budgeted these by up to 20x.
    ("deepseek-v4", 1_048_576), ("deepseek-r1-distill", 8_192),
    ("deepseek", 163_840),
    ("qwen3-coder-flash", 1_000_000), ("qwen-plus", 1_000_000),
    ("qwen3-max", 262_144), ("qwen3-coder", 262_144),
    ("qwen3-next", 262_144), ("qwen3", 131_072), ("qwen-2.5", 32_768),
    ("qwen", 32_768),
    ("mistral", 32_000),
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
    # "r1-distill-llama-70b" matched T2 on "r1" while carrying an 8k window.
    # A distillation is a smaller model trained on a larger one's output; it
    # must never inherit the parent's tier.
    distilled = "distill" in m
    if not distilled and (toks & TIER_HINTS["T2"]
                          or any(sub in m for sub in T2_SUBSTRINGS)):
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


def cap_tier_for_context(tier: str, ctx: int) -> str:
    """A tier is a promise about how much context the profile will use.

    T2 budgets 75% of the window and uses the widest action space. A model
    with a small window cannot honour that however capable it is, and
    `deepseek-r1-distill-llama-70b` ships an 8k window under a name that
    reads T2. Capping here is general: it protects against every future
    model that is strong but short.
    """
    if not ctx:
        return tier
    if ctx < 16_000:
        return "T0"
    if ctx < 33_000 and tier == "T2":
        return "T1"
    return tier


def ctx_of(model_id: str, reported: int | None = None) -> int:
    if reported:
        return int(reported)
    m = (model_id or "").lower()
    for pattern, ctx in CTX_HINTS:
        if pattern in m:
            return ctx
    return 32_000        # conservative floor


# Known coding ability, best first. Only consulted when the provider's own
# table does not decide -- which is exactly the aggregator case, where the
# catalogue is hundreds of models from every vendor.
#
# Without this the tie-break was the model id, alphabetically. On OpenRouter
# that selected `amazon/nova-pro-v1` out of 442 models because it begins with
# "a" -- an arbitrary choice presented as a considered one.
# Never auto-selected. An explicit HARNESS_MODEL still works -- FR-9 says
# an override wins outright -- but nothing here is chosen on the harness's
# own initiative. The evaluation runs on other families, and a harness that
# quietly reaches for a different vendor than the one it will be judged on
# is measuring the wrong thing.
EXCLUDED_DEFAULT = ("anthropic/", "claude", "openai/", "gpt-", "o3-", "o1-")


def excluded() -> tuple:
    """HARNESS_EXCLUDE_MODELS: comma-separated substrings, replacing the
    default. `HARNESS_EXCLUDE_MODELS=none` clears it."""
    import os
    raw = (os.environ.get("HARNESS_EXCLUDE_MODELS") or "").strip().lower()
    if not raw:
        return EXCLUDED_DEFAULT
    if raw in ("none", "off", "0", ""):
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def is_excluded(model_id: str) -> bool:
    m = (model_id or "").lower()
    return any(bad in m for bad in excluded())


PREFERRED = (
    # Families, newest-first within each, resolved against the live listing
    # at run time. Families change slowly; specific version names do not,
    # which is why only the family appears here.
    "deepseek-v4-pro", "deepseek-r1", "deepseek-v4", "deepseek-v3",
    "qwen3-max", "qwen3-coder", "qwen3-next", "qwen3-235b",
    "gemini-3-pro", "gemini-2.5-pro", "grok-4",
    "glm-5", "kimi-k2", "llama-4", "mistral-large",
)

# Shapes that are not a good default however capable the family is: previews
# and experiments change under you, and a vision or audio variant spends its
# capacity on a modality this harness never uses.
PENALISED = ("-exp", "-preview", "-vision", "-audio", "-image", ":free",
             "-thinking", "-online", "-beta", "-alpha", "-nightly",
             # Not interactive endpoints: a batch id accepts work and
             # answers later, which a fix-and-verify loop cannot use.
             ":batch", ":extended", ":thinking", ":nitro")

# Small models that are actually good at the verification role -- judging a
# diff, summarising. Taking the LAST entry of the ranking instead handed
# that job to the worst model in the catalogue, which on an aggregator is
# something nobody has heard of.
CHEAP_PREFERRED = (
    # Only a fallback: where the provider publishes prices they decide it.
    # Families again, not versions.
    "gemini-flash", "gemini-2.5-flash", "deepseek-v4-flash",
    "deepseek-chat", "qwen3-coder-flash", "qwen3-next", "mistral-nemo",
    "ministral", "granite-4",
)

VERSION = re.compile(r"(\d+(?:\.\d+)*)")


def _recency(model_id: str) -> tuple:
    """Newest first within a family.

    Ordering by id put `claude-opus-4.1` ahead of `claude-opus-4.7`: the
    same family, sorted as text, oldest wins.
    """
    nums = VERSION.findall(model_id or "")
    parts = []
    for chunk in nums[:3]:
        parts.extend(int(x) for x in chunk.split(".")[:3])
    # Fixed width, or a shorter tuple sorts first and "opus-5" beats
    # "opus-5.5" -- the older model winning because it has fewer digits.
    parts = (parts + [0, 0, 0, 0])[:4]
    return tuple(-n for n in parts)


def cheap_preference(model_id: str) -> int:
    m = (model_id or "").lower()
    penalty = len(CHEAP_PREFERRED) if any(p in m for p in PENALISED) else 0
    for i, needle in enumerate(CHEAP_PREFERRED):
        if needle in m:
            return i + penalty
    # Naming conventions outlive version names: a model called mini, nano,
    # flash or micro is the small one in its family, whatever the family is
    # called next year. This is what keeps the fallback working after the
    # list above goes stale -- which it will.
    if _tokens(m) & TIER_HINTS["T0"]:
        return len(CHEAP_PREFERRED) + penalty
    return len(CHEAP_PREFERRED) * 3 + penalty


def preference(model_id: str) -> int:
    """Lower is better. Unknown models sort after every known one."""
    m = (model_id or "").lower()
    rank = len(PREFERRED)
    for i, needle in enumerate(PREFERRED):
        if needle in m:
            rank = i
            break
    return rank + (len(PREFERRED) if any(p in m for p in PENALISED) else 0)


def sort_key(model_id: str, provider: str) -> tuple:
    """Total order: table position (longest match) first, then tier, then
    known coding ability, then the id as a last resort."""
    hit = _table_match(model_id, provider)
    if hit:
        return (0, hit[0], preference(model_id), _recency(model_id),
                model_id)
    return (1, TIER_ORDER[tier_of(model_id, provider)],
            preference(model_id), _recency(model_id), model_id)


def rank_models(available: list[str], provider: str) -> list[str]:
    """Chat-capable models, best first, under a deterministic total order.

    Exclusions apply to the chat-capable set, not to the raw listing: a key
    offering only excluded families plus an image model must still return
    the excluded ones rather than nothing at all. Never being able to run is
    worse than running on a family the operator would rather avoid.
    """
    chat = [m for m in available if chat_capable(m)]
    kept = [m for m in chat if not is_excluded(m)] or chat
    return sorted(kept, key=lambda m: sort_key(m, provider))


def select_models(available: list[str], provider: str,
                  model_override: str | None = None,
                  cheap_override: str | None = None,
                  price: dict | None = None,
                  ctx: dict | None = None) -> tuple[str, str]:
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
    # The cheap role judges diffs and summarises. Picking the tail of the
    # ranking gave that to whatever the catalogue happened to end with.
    # What the provider says it costs beats what this file remembers a
    # cheap model is called. The name list is the fallback for providers
    # that publish no prices.
    if price:
        # Cheapest, but it still has to hold a diff and a prompt. Without a
        # window floor the cheapest entry in a catalogue is often a toy.
        priced = [m for m in ordered
                  if price.get(m) and not any(p in m.lower()
                                              for p in PENALISED)
                  and ctx_of(m, (ctx or {}).get(m)) >= CHEAP_MIN_CTX]
        if priced:
            by_cheap = sorted(priced, key=lambda m: (price[m], m))
        else:
            by_cheap = sorted(ordered, key=lambda m: (cheap_preference(m),
                                                      _recency(m), m))
    else:
        by_cheap = sorted(ordered, key=lambda m: (cheap_preference(m),
                                                  _recency(m), m))
    cheap = cheap_override or (by_cheap[0] if by_cheap else primary)
    if cheap == primary and len(by_cheap) > 1:
        cheap = by_cheap[1]
    return primary, cheap
