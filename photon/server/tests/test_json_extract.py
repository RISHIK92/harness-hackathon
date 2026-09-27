"""extract_json's salvage paths, built from the ACTUAL malformed planner
output collected out of eval logs — not invented shapes.

The grammar (llm.PLAN_SCHEMA) is what stops these being produced at all;
this is the net under it, for the one case a grammar cannot prevent — output
truncated at max_tokens.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.llm import extract_json  # noqa: E402

VALID = '{"calls": [{"tool": "search_code", "args": {"query": "webhook retry"}}]}'


def test_valid_json_is_unchanged():
    assert extract_json(VALID) == {"calls": [{"tool": "search_code", "args": {"query": "webhook retry"}}]}


def test_markdown_fences_are_stripped():
    assert extract_json("```json\n" + VALID + "\n```")["calls"][0]["tool"] == "search_code"


# ── observed verbatim in eval runs ───────────────────────────────────────

def test_an_extra_closing_brace():
    """The most common one. A greedy {.*} regex ran to the LAST brace and
    swallowed the junk; a balanced scan stops at the right one."""
    assert len(extract_json(VALID[:-1] + "}}")["calls"]) == 1


def test_a_stray_token_where_the_brace_belongs():
    for junk in ("]5}", "] K}", "]absolut}", "]uretics}", "]>}", "]^{}"):
        text = '{"calls": [{"tool": "search_docs", "args": {"query": "rotate secret"}}' + junk
        assert extract_json(text) is not None, junk
        assert extract_json(text)["calls"][0]["tool"] == "search_docs"


def test_output_truncated_before_it_closed():
    """The one shape a decoding grammar cannot prevent — the token budget ran
    out mid-object — so the salvage path has to survive this one."""
    text = '{"calls": [{"tool": "search_code", "args": {"query": "webhook retry"}}]'
    assert extract_json(text)["calls"][0]["args"]["query"] == "webhook retry"


def test_two_arrays_are_merged_not_halved():
    text = ('{"calls": [{"tool": "get_account", "args": {"account_id": "acct_calico"}}], '
            '[{"tool": "search_code", "args": {"query": "Calico rate"}}]}')
    tools = [c["tool"] for c in extract_json(text)["calls"]]
    assert tools == ["get_account", "search_code"]


# ── things it must NOT do ────────────────────────────────────────────────

def test_a_brace_inside_a_value_does_not_end_the_scan():
    text = '{"calls": [{"tool": "search_code", "args": {"query": "the {} literal"}}]}}'
    assert extract_json(text)["calls"][0]["args"]["query"] == "the {} literal"


def test_an_array_in_an_argument_is_not_mistaken_for_a_call_list():
    text = '{"calls": [{"tool": "search_code", "args": {"tags": ["a", "b"]}}]xx'
    calls = extract_json(text)["calls"]
    assert len(calls) == 1 and calls[0]["args"]["tags"] == ["a", "b"]


def test_genuine_nonsense_still_returns_none():
    """Salvage must not invent a plan out of nothing — an empty plan is a
    real signal (it ends the loop) and faking one would be worse."""
    assert extract_json("I'm sorry, I can't help with that.") is None
    assert extract_json('{"calls": [{"broken"') is None
