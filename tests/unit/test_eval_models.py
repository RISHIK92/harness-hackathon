"""DeepSeek and Qwen, the families the evaluation will actually use.

Model ids move. These pin behaviour against the catalogue as it stands, and
against the two shapes that break a harness which has only ever seen
OpenAI-style replies: reasoning traces in the content, and a strong model
shipping a small window.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.model.adapters.openai_compat import (strip_reasoning,       # noqa: E402
                                                  visible_text)
from harness.model.rank import (cap_tier_for_context, ctx_of,            # noqa: E402
                                tier_of)


# -- reasoning output ------------------------------------------------------

def test_inline_thinking_is_removed_before_parsing():
    """R1 and QwQ put their working out in front of the answer. Left in, a
    JSON reply does not parse and an edit block has prose before it."""
    assert strip_reasoning(
        "<think>95 is >= 90 so it counts.</think>"
        '{"classification": "missing_case"}') == '{"classification": "missing_case"}'


@pytest.mark.parametrize("tag", ["think", "thinking", "reason", "reasoning"])
def test_every_reasoning_tag_is_handled(tag):
    assert strip_reasoning(f"<{tag}>hidden</{tag}>visible") == "visible"


def test_a_reasoning_block_before_an_edit_does_not_corrupt_it():
    reply = ("<think>\nI should guard the empty case.\n</think>\n\n"
             "<<<<<<< SEARCH a.js\nold\n=======\nnew\n>>>>>>> REPLACE")
    out = strip_reasoning(reply)
    assert out.startswith("<<<<<<< SEARCH")
    assert "I should guard" not in out


def test_an_unterminated_block_is_a_truncated_reply_not_an_answer():
    """Cut off at max_tokens mid-thought. Returning the monologue would have
    the parser treat reasoning as a fix."""
    assert strip_reasoning("<think>I was cut off before finishing") == ""


def test_text_without_reasoning_is_untouched():
    assert strip_reasoning('{"plain": 1}') == '{"plain": 1}'
    assert strip_reasoning("") == ""


def test_deepseeks_separate_reasoning_field_is_not_concatenated():
    """Its own API returns reasoning_content beside content."""
    assert visible_text({"content": '{"a": 1}',
                         "reasoning_content": "a long chain of thought"}) \
        == '{"a": 1}'


# -- tiering, against the catalogue as it stands ---------------------------

@pytest.mark.parametrize("model,expected", [
    ("deepseek/deepseek-v4-pro", "T2"),
    ("deepseek/deepseek-r1-0528", "T2"),
    ("deepseek/deepseek-chat-v3.1", "T1"),
    ("deepseek/deepseek-v3.2", "T1"),
    ("deepseek/deepseek-v4-flash", "T1"),
    ("deepseek-reasoner", "T2"),
    ("deepseek-chat", "T1"),
])
def test_deepseek_tiers(model, expected):
    assert tier_of(model, "deepseek") == expected


@pytest.mark.parametrize("model,expected", [
    ("qwen/qwen3-max", "T2"),
    ("qwen/qwen3-max-thinking", "T2"),
    ("qwen/qwen3-235b-a22b", "T2"),
    ("qwen/qwen3-coder-plus", "T2"),
    ("qwen/qwen3-coder-flash", "T1"),
    ("qwen/qwen-plus", "T1"),
    ("qwen/qwen-2.5-coder-32b-instruct", "T1"),
    ("qwen/qwen3-8b", "T0"),
    ("qwen-turbo", "T0"),
])
def test_qwen_tiers(model, expected):
    assert tier_of(model, "qwen") == expected


def test_a_distill_does_not_inherit_the_parent_tier():
    """`r1-distill-llama-70b` read as T2 on "r1" while shipping 8k."""
    assert tier_of("deepseek/deepseek-r1-distill-llama-70b", "deepseek") != "T2"


def test_a_small_window_caps_the_tier_whatever_the_name_says():
    """T2 budgets 75% of the window and uses the widest action space; an 8k
    model cannot honour that promise."""
    assert cap_tier_for_context("T2", 8_192) == "T0"
    assert cap_tier_for_context("T2", 32_000) == "T1"
    assert cap_tier_for_context("T2", 163_840) == "T2"
    assert cap_tier_for_context("T1", 200_000) == "T1"
    assert cap_tier_for_context("T2", 0) == "T2", "unknown ctx must not demote"


@pytest.mark.parametrize("model,at_least", [
    ("deepseek/deepseek-chat-v3.1", 163_840),
    ("deepseek/deepseek-v4-pro", 1_000_000),
    ("qwen/qwen3-max", 262_144),
    ("qwen/qwen3-235b-a22b", 131_072),
])
def test_context_fallbacks_are_not_decades_out_of_date(model, at_least):
    """deepseek=65k and qwen=32k under-budgeted these by up to twenty times,
    which throws away most of the window when a provider reports nothing."""
    assert ctx_of(model) >= at_least


def test_a_reported_window_always_wins_over_the_guess():
    assert ctx_of("deepseek/deepseek-chat", reported=64_000) == 64_000


def test_qwen_has_somewhere_to_send_a_key():
    from harness.model.detect import PROVIDERS
    assert "qwen" in PROVIDERS
    assert PROVIDERS["qwen"].wire == "openai"


# -- choosing from an aggregator's catalogue --------------------------------
#
# With 442 chat-capable models and no provider table, the tie-break was the
# model id. That selected `amazon/nova-pro-v1` because it begins with "a" --
# an arbitrary choice presented as a considered one.

CATALOGUE = [
    "amazon/nova-pro-v1", "anthropic/claude-opus-4.1",
    "anthropic/claude-opus-4.1:batch", "anthropic/claude-opus-5.5",
    "anthropic/claude-haiku-4.5", "deepseek/deepseek-v4-pro",
    "qwen/qwen3-max", "openai/gpt-5", "openai/gpt-5-mini",
    "thinkingmachines/inkling-small:free", "zzz/unknown-large",
]


def test_a_known_coding_model_beats_an_alphabetical_accident():
    from harness.model.rank import rank_models
    best = rank_models(CATALOGUE, "openrouter")[0]
    assert best != "amazon/nova-pro-v1"
    # Anthropic and OpenAI are excluded from auto-selection, so the best
    # remaining known coding family should win -- not an alphabetical one.
    assert "deepseek" in best or "qwen" in best


def test_the_newest_version_in_a_family_wins():
    """Ordering by id put claude-opus-4.1 ahead of 5.5: same family, sorted
    as text, oldest first."""
    from harness.model.rank import rank_models
    catalogue = ["vendor/thing-4.1", "vendor/thing-5.5", "vendor/thing-4.8"]
    ranked = rank_models(catalogue, "openrouter")
    assert ranked[0] == "vendor/thing-5.5"


def test_batch_endpoints_are_not_chosen():
    """A batch id accepts work and answers later; a fix-and-verify loop
    cannot use that."""
    from harness.model.rank import select_models
    primary, cheap = select_models(CATALOGUE, "openrouter")
    assert ":batch" not in primary and ":batch" not in cheap


def test_the_cheap_model_is_a_real_small_model():
    """It was the LAST entry of the ranking -- the worst model in the
    catalogue, judging every diff."""
    from harness.model.rank import select_models
    catalogue = ["deepseek/deepseek-v4-pro", "qwen/qwen3-coder-flash",
                 "thinkingmachines/inkling-small:free"]
    _, cheap = select_models(catalogue, "openrouter")
    assert cheap != "thinkingmachines/inkling-small:free", \
        "a :free variant of an unknown model is not a considered choice"
    assert any(k in cheap for k in ("haiku", "mini", "flash", "nano"))


def test_primary_and_cheap_are_not_the_same_model():
    from harness.model.rank import select_models
    primary, cheap = select_models(CATALOGUE, "openrouter")
    assert primary != cheap


def test_the_evaluation_families_rank_above_unknown_vendors():
    """DeepSeek and Qwen are what the evaluation uses; they must not sit
    behind a vendor nobody has heard of."""
    from harness.model.rank import rank_models
    ranked = rank_models(CATALOGUE, "openrouter")
    for known in ("deepseek/deepseek-v4-pro", "qwen/qwen3-max"):
        assert ranked.index(known) < ranked.index("zzz/unknown-large")
        assert ranked.index(known) < ranked.index("amazon/nova-pro-v1")


def test_an_override_still_wins_outright():
    """FR-9: a proxy may serve ids it does not advertise."""
    from harness.model.rank import select_models
    primary, cheap = select_models(CATALOGUE, "openrouter",
                                   model_override="deepseek/deepseek-r1",
                                   cheap_override="qwen/qwen-turbo")
    assert primary == "deepseek/deepseek-r1"
    assert cheap == "qwen/qwen-turbo"


def test_a_published_price_decides_the_cheap_model_not_a_name_list():
    """Every name in a hardcoded "cheap models" list is a guess about a
    catalogue that changes weekly. The listing states the price."""
    from harness.model.rank import CHEAP_MIN_CTX, select_models

    catalogue = ["deepseek/deepseek-v4-pro", "vendor/expensive-small",
                 "vendor/actually-cheapest", "qwen/qwen3-coder-flash"]
    price = {"deepseek/deepseek-v4-pro": 15e-6,
             "vendor/expensive-small": 2e-6,
             "vendor/actually-cheapest": 0.02e-6,
             "qwen/qwen3-coder-flash": 0.3e-6}
    ctx = {m: 131_072 for m in catalogue}

    _, cheap = select_models(catalogue, "openrouter", price=price, ctx=ctx)
    assert cheap == "vendor/actually-cheapest", \
        "ignored the published price in favour of a remembered name"


def test_the_cheap_model_must_still_hold_a_diff():
    """Without a window floor the cheapest entry in a catalogue is a toy."""
    from harness.model.rank import CHEAP_MIN_CTX, select_models

    catalogue = ["deepseek/deepseek-v4-pro", "vendor/tiny-window",
                 "qwen/qwen3-coder-flash"]
    price = {"deepseek/deepseek-v4-pro": 15e-6,
             "vendor/tiny-window": 0.001e-6,
             "qwen/qwen3-coder-flash": 0.3e-6}
    ctx = {"deepseek/deepseek-v4-pro": 200_000,
           "vendor/tiny-window": 4_096,
           "qwen/qwen3-coder-flash": 400_000}

    _, cheap = select_models(catalogue, "openrouter", price=price, ctx=ctx)
    assert cheap != "vendor/tiny-window"
    assert ctx[cheap] >= CHEAP_MIN_CTX


def test_without_prices_the_name_fallback_still_works():
    """Most providers publish no pricing at all."""
    from harness.model.rank import select_models
    catalogue = ["deepseek/deepseek-v4-pro", "qwen/qwen3-coder-flash"]
    _, cheap = select_models(catalogue, "openrouter")
    assert "flash" in cheap


def test_discovery_carries_the_price_it_was_given():
    from harness.model.adapters.openai_compat import input_price
    assert input_price({"pricing": {"prompt": "0.000002"}}) == 2e-6
    assert input_price({"pricing": {"prompt": "0"}}) is None
    assert input_price({}) is None


def test_excluded_families_are_not_auto_selected():
    """`never use claude/gpt models` -- they must not be chosen on the
    harness's own initiative."""
    from harness.model.rank import rank_models
    catalogue = ["anthropic/claude-opus-5.5", "openai/gpt-5",
                 "deepseek/deepseek-v4-pro", "qwen/qwen3-max"]
    ranked = rank_models(catalogue, "openrouter")
    assert not any("claude" in m or "gpt-" in m for m in ranked)


def test_an_excluded_family_is_still_usable_when_named():
    """FR-9: an override wins outright. Excluding is about initiative, not
    capability."""
    from harness.model.rank import select_models
    primary, _ = select_models(["deepseek/deepseek-v4-pro"], "openrouter",
                               model_override="openai/gpt-5")
    assert primary == "openai/gpt-5"


def test_exclusion_never_leaves_the_run_with_no_model():
    """A key offering only excluded families must still run. Never being
    able to start is worse than using a family you would rather avoid."""
    from harness.model.rank import rank_models
    ranked = rank_models(["openai/gpt-5", "openai/gpt-5-mini"], "openai")
    assert ranked, "excluded everything and left nothing"


def test_the_exclusion_list_is_configurable(monkeypatch):
    from harness.model.rank import rank_models
    monkeypatch.setenv("HARNESS_EXCLUDE_MODELS", "none")
    ranked = rank_models(["anthropic/claude-opus-5.5",
                          "deepseek/deepseek-v4-pro"], "openrouter")
    assert any("claude" in m for m in ranked)
