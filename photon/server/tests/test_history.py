"""Conversation memory (app.agent.history) — no network, no LLM, no DB.

The point of these is the SPLIT the module is built around: what the model
reads (the raw question plus a history block) and what the search tools get
(a self-contained string) are different, and every bug this feature can have
is a case where one is used as the other.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.history import (  # noqa: E402
    format_history_block,
    is_follow_up,
    normalise_history,
    resolve_query,
    strip_markers,
)

HISTORY = [
    {"role": "user", "text": "why does pricing have a special case for Bangalore"},
    {"role": "agent", "text": "There's a reseller agreement with a partner there."},
]


def test_no_history_means_nothing_changes():
    """A first turn must be byte-for-byte what it was before this existed."""
    assert resolve_query("why is that?", []) == ("why is that?", False)
    assert format_history_block([]) == ""
    assert is_follow_up("why is that?", []) is False


def test_anaphora_is_a_follow_up():
    for q in ["why is that?", "who decided it", "is this documented", "are they still failing"]:
        assert is_follow_up(q, HISTORY), q


def test_elliptical_openers_are_follow_ups():
    for q in ["and Northwind?", "what about the other one", "tell me more", "how come"]:
        assert is_follow_up(q, HISTORY), q


def test_a_complete_question_is_not_a_follow_up():
    """The failure mode this guards: a standalone question opening with
    "why" getting the previous one glued to the front of its search query,
    blurring a perfectly good retrieval."""
    for q in [
        "why does pricing have a special case for Bangalore",
        "what is the webhook retry policy in the docs",
        "is Northwind on the partner tier",
    ]:
        assert not is_follow_up(q, HISTORY), q


def test_short_indic_follow_up():
    assert is_follow_up("ఎందుకు?", HISTORY)


def test_resolve_query_prepends_the_last_user_question():
    query, resolved = resolve_query("why is that?", HISTORY)
    assert resolved is True
    assert "Bangalore" in query and "why is that?" in query


def test_resolve_query_ignores_the_agents_own_turn():
    """The referent is the last thing the CALLER asked, not the last thing
    said — an agent turn is the answer, not the subject."""
    history = HISTORY + [{"role": "agent", "text": "Anything else?"}]
    query, _ = resolve_query("and why is that?", history)
    assert query.startswith("why does pricing")


def test_citation_markers_never_survive_into_memory():
    """An [ev_xxx] id is valid only for the turn that produced it. Left in
    memory, the compose model reuses it this turn where it names nothing,
    and the verifier then strips a correct claim as uncited."""
    assert strip_markers("Bangalore has a partner rate [ev_80abd768].") == "Bangalore has a partner rate."
    assert strip_markers("both [ev_1a2b3c4d, ev_5e6f7a8b] here") == "both here"
    turns = normalise_history([{"role": "agent", "text": "It is a partner rate [ev_deadbeef]."}])
    assert "ev_" not in turns[0]["text"]


def test_normalise_history_is_defensive_about_client_input():
    turns = normalise_history(
        [
            {"role": "assistant", "text": "  mapped to agent  "},
            {"role": "nonsense", "text": "mapped to user"},
            {"role": "user", "text": ""},  # dropped
            {"role": "user"},  # dropped
        ]
    )
    assert [t["role"] for t in turns] == ["agent", "user"]
    assert turns[0]["text"] == "mapped to agent"


def test_history_is_capped():
    turns = normalise_history([{"role": "user", "text": f"q{i}"} for i in range(50)])
    assert len(turns) == 6
    assert turns[-1]["text"] == "q49"  # the most recent turns are the ones kept

    long_turn = normalise_history([{"role": "user", "text": "x" * 5000}])
    assert len(long_turn[0]["text"]) == 400


def test_history_block_labels_both_sides():
    block = format_history_block(HISTORY)
    assert "Caller: why does pricing" in block
    assert "You: There's a reseller" in block
