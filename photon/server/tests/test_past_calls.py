"""Memory across calls (app.tools.memory) — the pure half, no DB needed.

The DB half is one query; the part that can silently produce a wrong
citation is the pairing and the scoring, which is what these cover.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.loop import _RECALL_RE, _fast_path_calls  # noqa: E402
from app.tools.memory import _locator, _pair_turns, _score, _stem, _to_evidence, rank  # noqa: E402


def row(role, text, slug="abcd-efgh", minute=0):
    return {
        "role": role,
        "text": text,
        "speaker_name": "Priya",
        "created_at": datetime(2026, 9, 11, 14, minute, 0),
        "slug": slug,
    }


def test_a_question_is_paired_with_the_reply_that_followed_it():
    pairs = _pair_turns([row("human", "why do webhooks retry three times"), row("agent", "The code does 3 retries.")])
    assert len(pairs) == 1
    assert pairs[0]["answer"] == "The code does 3 retries."


def test_an_answer_is_never_attached_across_another_question():
    """The wrong-citation failure: scanning forward for "the next agent line"
    would report a reply that was given to something else entirely."""
    pairs = _pair_turns(
        [
            row("human", "first question"),
            row("human", "second question"),
            row("agent", "this answers the SECOND one"),
        ]
    )
    assert pairs[0]["answer"] == ""
    assert pairs[1]["answer"] == "this answers the SECOND one"


def test_an_unanswered_question_is_kept():
    """A question the agent abstained on is real memory, and often the most
    useful line in a transcript — dropping it would hide exactly that."""
    pairs = _pair_turns([row("human", "what is the refund window")])
    assert len(pairs) == 1
    assert pairs[0]["answer"] == ""
    assert "no answer was recorded" in _to_evidence(pairs[0], 0.5)["snippet"]


def test_scoring_ignores_words_every_question_contains():
    assert _score({"webhook", "retries"}, "what is the thing that we do") == 0.0
    assert _score({"webhook", "retries"}, "the webhook retries three times") > 0.0


def test_plural_and_verb_forms_still_match():
    """Found live, on the first real transcript this was pointed at: the
    query "webhook retries" scored ZERO against "why do the webhooks only
    retry three times" — the exact question it was recalling. Every term was
    present and none of them matched. Drift between how a thing is asked
    about now and how it was asked about last time is the normal case."""
    assert _score(_tokens("webhook retries"), "why do the webhooks only retry three times") > 0
    assert _stem("retries") == _stem("retry")
    assert _stem("webhooks") == _stem("webhook")


def _tokens(text):
    from app.tools.memory import _tokenise

    return _tokenise(text)


def test_ranking_prefers_the_question_that_shares_vocabulary():
    pairs = _pair_turns(
        [
            row("human", "how does Bangalore pricing work"),
            row("agent", "Partner rate."),
            row("human", "why do webhooks retry"),
            row("agent", "Three retries."),
        ]
    )
    ranked = rank("webhook retry behaviour", pairs, top_k=5)
    assert ranked[0][0]["text"] == "why do webhooks retry"


def test_ranking_scores_the_question_not_the_answer():
    """The caller is recalling what they ASKED. A long answer scoring the
    pair would drag in a question about something else."""
    pairs = [
        {**row("human", "how is billing prorated"), "answer": "webhook webhook webhook retry retry"},
        {**row("human", "why do webhooks retry"), "answer": "short"},
    ]
    assert rank("webhook retry", pairs, top_k=1)[0][0]["text"] == "why do webhooks retry"


def test_nothing_matching_returns_nothing():
    """No floor-scraping best guess: an unrelated past call surfaced as
    "memory" is a fabricated recollection, and abstaining is the rule."""
    pairs = _pair_turns([row("human", "how is billing prorated")])
    assert rank("kubernetes ingress certificates", pairs, top_k=5) == []


def test_the_locator_names_a_real_checkable_line():
    """It must resolve against GET /api/meetings/{slug}/transcript.md —
    that is what makes this citable under the never-fabricate rule."""
    assert _locator(row("human", "x")) == "call:abcd-efgh:2026-09-11T14:00:00Z"


def test_evidence_is_a_verbatim_quote_of_both_sides():
    ev = _to_evidence({**row("human", "why do webhooks retry"), "answer": "Three retries."}, 0.7)
    assert ev["source_type"] == "call"
    assert "Priya asked: why do webhooks retry" in ev["snippet"]
    assert "answered: Three retries." in ev["snippet"]


def test_recall_shaped_questions_reach_the_memory_tool_on_the_fast_path():
    """The fast path skips the planner entirely, so it has to apply the
    plan prompt's own rule itself or the tool is simply never called."""
    allowed = {"search_code", "search_past_calls"}
    calls = _fast_path_calls("what did we tell them about this last time", allowed)
    assert [c["tool"] for c in calls] == ["search_code", "search_past_calls"]


def test_an_ordinary_code_question_does_not_search_old_calls():
    calls = _fast_path_calls("where is the retry backoff defined", {"search_code", "search_past_calls"})
    assert "search_past_calls" not in [c["tool"] for c in calls]


def test_memory_tool_does_not_disable_the_fast_path():
    """It is default-on for any workspace that has had a call, so leaving it
    out of _CODE_TOOLS would have switched the ~1.8s fast path off for every
    code-only workspace the moment it finished its first call."""
    assert _fast_path_calls("where is retry defined", {"search_code", "search_past_calls"}) is not None


def test_recall_pattern_does_not_fire_on_ordinary_past_tense():
    assert not _RECALL_RE.search("what happened before the retry")
    assert _RECALL_RE.search("did we discuss this before")


def test_a_follow_up_is_searched_as_the_resolved_question():
    """The fast path's whole risk: with no planner, "why is that?" would
    otherwise be embedded as three pronouns and match nothing."""
    calls = _fast_path_calls("why is that?", {"search_code", "explain_why"}, "why is the Bangalore rate different why is that?")
    assert calls[0]["args"]["query"] == "why is the Bangalore rate different why is that?"
    assert calls[1]["args"]["symbol_or_path"] == "why is the Bangalore rate different why is that?"
