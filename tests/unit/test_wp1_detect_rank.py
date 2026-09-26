"""T1.1/T1.3/T1.4: provider detection, chat filtering, ranking, overrides.

These run with the network unavailable -- detection must not need it (FR-6).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.model.detect import (AMBIGUOUS, PROVIDERS, detect_provider,
                                  resolve)
from harness.model.rank import (NoChatModel, chat_capable, ctx_of, rank_models,
                                select_models, tier_of)


# -- FR-6 detection --------------------------------------------------------
@pytest.mark.parametrize("key,expected", [
    ("sk-ant-api03-FAKEabcdef1234567890", "anthropic"),
    ("sk-ant-FAKEabcdef1234567890", "anthropic"),
    ("sk-or-v1-FAKE0123456789abcdef01", "openrouter"),
    ("sk-proj-FAKEabcdefghijklmnop12", "openai"),
    ("sk-svcacct-FAKEabcdefghij1234", "openai"),
    ("sk-admin-FAKEabcdefghij1234", "openai"),
    ("gsk_FAKEabcdefghijklmnop34", "groq"),
    ("xai-FAKEabcdefghijklmnop34", "xai"),
    ("csk-FAKEabcdefghijklmnop12", "cerebras"),
    ("AIzaSyFAKEabcdefghijklmnopqrstuvwx23", "google"),
    ("tgp_v1_FAKEabcdefghijklmnop12", "together"),
    ("fw_FAKEabcdefghijklmnop1234", "fireworks"),
    ("sk-AbCdEfGhIjKlMnOpQrStUv", AMBIGUOUS),
    ("", "openai_compatible"),
    ("no-known-prefix-at-all", "openai_compatible"),
])
def test_prefix_detection(key, expected):
    assert detect_provider(key) == expected


def test_openrouter_key_is_not_mistaken_for_openai():
    """The single most common BYOK bug: sk-or-v1- must beat sk-."""
    assert detect_provider("sk-or-v1-FAKEabcdef0123456789") == "openrouter"
    p = resolve("sk-or-v1-FAKEabcdef0123456789")
    assert "openrouter.ai" in p.base_url


def test_project_key_is_not_ambiguous():
    assert detect_provider("sk-proj-FAKEabcdef0123456789") == "openai"


def test_longest_prefix_wins_over_short():
    assert detect_provider("sk-ant-api03-xyz123456789") == "anthropic"


def test_detection_makes_no_network_call(monkeypatch):
    import socket

    def boom(*a, **k):
        raise AssertionError("detection must not touch the network (FR-6)")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    assert detect_provider("sk-ant-api03-FAKEabc123456789") == "anthropic"


def test_ambiguous_ladder_prefers_base_url():
    p = resolve("sk-FAKEabcdefghij1234567", base_url="http://localhost:11434/v1")
    assert p.base_url == "http://localhost:11434/v1"
    assert p.wire == "openai"


def test_ambiguous_ladder_uses_prober_in_fixed_order():
    seen = []

    def prober(provider):
        seen.append(provider.name)
        return provider.name == "deepseek"

    p = resolve("sk-FAKEabcdefghij1234567", prober=prober)
    assert p.name == "deepseek"
    assert seen == ["openai", "deepseek"]


def test_ambiguous_ladder_defaults_to_openai():
    p = resolve("sk-FAKEabcdefghij1234567", prober=lambda _p: False)
    assert p.name == "openai"


def test_forced_provider_overrides_prefix():
    p = resolve("sk-ant-api03-FAKEabc123456789", forced="groq")
    assert p.name == "groq"


def test_base_url_selects_openai_wire(): 
    """FR-10: AI_BASE_URL means an OpenAI-compatible endpoint, whatever the
    key prefix says. Talking the anthropic wire to an OpenAI gateway silently
    returns empty replies."""
    p = resolve("sk-ant-api03-FAKEabc123456789", base_url="https://proxy.local/v1")
    assert p.base_url == "https://proxy.local/v1"
    assert p.wire == "openai"


def test_harness_provider_overrides_base_url_wire():
    p = resolve("sk-ant-api03-FAKEabc123456789", base_url="https://proxy.local",
                forced="anthropic")
    assert p.wire == "anthropic"
    assert p.base_url == "https://proxy.local"


# -- FR-7 chat filter ------------------------------------------------------
@pytest.mark.parametrize("mid", [
    "text-embedding-3-large", "whisper-1", "tts-1-hd", "dall-e-3",
    "omni-moderation-latest", "BAAI/bge-large-en", "nomic-embed-text",
    "stable-diffusion-xl", "gpt-4o-realtime-preview", "llama-guard-3-8b",
])
def test_non_chat_models_filtered(mid):
    assert chat_capable(mid) is False


@pytest.mark.parametrize("mid", [
    "claude-opus-5-5", "gpt-5", "gemini-2.5-pro", "llama-3.3-70b-versatile",
    "deepseek-reasoner", "mistral-large-latest", "grok-4",
])
def test_chat_models_kept(mid):
    assert chat_capable(mid) is True


def test_dalle_can_never_be_selected_as_primary():
    """An unsorted, unfiltered fallback picks dall-e-3. This must not happen."""
    listing = ["dall-e-3", "text-embedding-3-small", "whisper-1",
               "gpt-4o-mini", "gpt-5"]
    primary, cheap = select_models(listing, "openai")
    assert primary == "gpt-5"
    assert cheap == "gpt-4o-mini"


def test_no_chat_model_raises():
    with pytest.raises(NoChatModel):
        select_models(["dall-e-3", "whisper-1"], "openai")


# -- FR-8 ranking ----------------------------------------------------------
def test_primary_is_best_and_cheap_is_worst():
    models = ["claude-haiku-4-5-20251001", "claude-opus-5-5", "claude-sonnet-5"]
    primary, cheap = select_models(models, "anthropic")
    assert primary == "claude-opus-5-5"
    assert "haiku" in cheap


def test_selection_is_order_independent():
    """Shuffled input must give identical output (NFR-5)."""
    import random
    models = ["gpt-4o", "gpt-5", "gpt-4o-mini", "o3", "gpt-3.5-turbo"]
    first = select_models(list(models), "openai")
    for seed in range(20):
        shuffled = list(models)
        random.Random(seed).shuffle(shuffled)
        assert select_models(shuffled, "openai") == first


def test_unknown_provider_still_totally_ordered():
    models = ["zeta-large-v2", "alpha-8b", "beta-pro"]
    a = select_models(list(models), "unknown-provider")
    b = select_models(list(reversed(models)), "unknown-provider")
    assert a == b
    assert a[0] == "beta-pro"       # T2 by hint
    assert a[1] == "alpha-8b"       # T0 by hint


@pytest.mark.parametrize("mid,tier", [
    ("claude-opus-5-5", "T2"), ("claude-sonnet-5", "T1"),
    ("claude-haiku-4-5-20251001", "T0"), ("gpt-5", "T2"),
    ("gpt-4o-mini", "T0"), ("gemini-2.5-pro", "T2"),
    ("gemini-2.5-flash", "T1"), ("gemini-2.0-flash-lite", "T0"),
    ("llama-3.1-8b-instant", "T0"), ("llama-3.3-70b-versatile", "T1"),
    ("deepseek-reasoner", "T2"), ("some-private-deployment", "T1"),
])
def test_tier_inference(mid, tier):
    provider = {"claude": "anthropic", "gpt": "openai", "gemini": "google",
                "llama": "groq", "deepseek": "deepseek"}.get(
                    mid.split("-")[0], "")
    assert tier_of(mid, provider) == tier


def test_dated_snapshot_ids_match_their_family():
    assert tier_of("claude-opus-5-5-20260401", "anthropic") == "T2"
    assert tier_of("gpt-4o-mini-2024-07-18", "openai") == "T0"


# -- FR-9 overrides --------------------------------------------------------
def test_override_wins_even_if_absent_from_listing():
    primary, cheap = select_models(
        ["gpt-4o"], "openai",
        model_override="private-model-v3",
        cheap_override="private-mini")
    assert primary == "private-model-v3"
    assert cheap == "private-mini"


def test_single_override_keeps_discovery_for_the_other():
    primary, cheap = select_models(
        ["gpt-5", "gpt-4o-mini"], "openai", model_override="o3-pro")
    assert primary == "o3-pro"
    assert cheap == "gpt-4o-mini"


def test_override_works_when_listing_is_empty():
    primary, cheap = select_models([], "openai", model_override="only-model")
    assert primary == cheap == "only-model"


# -- context hints ---------------------------------------------------------
def test_ctx_reported_wins_over_hint():
    assert ctx_of("claude-opus-5-5", reported=64_000) == 64_000
    assert ctx_of("claude-opus-5-5") == 200_000
    assert ctx_of("totally-unknown-model") == 32_000


def test_rank_models_drops_non_chat():
    out = rank_models(["gpt-5", "text-embedding-3-large", "gpt-4o-mini"],
                      "openai")
    assert out == ["gpt-5", "gpt-4o-mini"]
