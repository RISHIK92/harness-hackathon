"""Proves app.agent.loop is callable standalone, with no call/session/room
in progress and no transport code anywhere in the package (CLAUDE.md
Section 5 / Definition of Done). Requires the local stack running (Postgres,
Neo4j, Qdrant, Gemini reachable) and the seed corpus loaded — see
`python3 -c "from app.seed.loader import load_all; load_all()"`.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

from app.agent.loop import answer_question
from app.seed.loader import get_seed_repo_id

AGENT_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "agent"
FORBIDDEN_IMPORT_PATTERNS = re.compile(r"livekit|room|call_session|webrtc", re.IGNORECASE)


def test_agent_package_has_zero_transport_imports():
    """Static check: no file under app/agent/ imports anything transport-shaped."""
    py_files = list(AGENT_DIR.glob("*.py"))
    assert py_files, "expected app/agent/*.py to exist"

    for path in py_files:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not FORBIDDEN_IMPORT_PATTERNS.search(name), (
                    f"{path.name} imports {name!r} — app/agent must have zero transport imports"
                )


@pytest.fixture(scope="module")
def repo_id() -> str:
    rid = get_seed_repo_id()
    if not rid:
        pytest.skip("seed repo not ingested — run `python3 -c 'from app.seed.loader import load_all; load_all()'` first")
    return rid


def _assert_contract_shape(result: dict) -> None:
    for key in ("answer", "claims", "confidence", "abstained", "escalation", "tool_trace"):
        assert key in result, f"missing '{key}' in agent answer contract"
    assert result["confidence"] in ("high", "medium", "low")
    assert isinstance(result["abstained"], bool)
    assert isinstance(result["claims"], list)
    assert isinstance(result["tool_trace"], list)
    for c in result["claims"]:
        assert "evidence_ids" in c and c["evidence_ids"], "every surviving claim must carry evidence_ids"


async def test_s1_northwind_webhook_question_grounds_in_real_evidence(repo_id):
    result = await answer_question(
        "Northwind says their webhooks stopped firing, what's going on?", repo_id=repo_id
    )
    _assert_contract_shape(result)
    assert result["abstained"] is False
    assert result["confidence"] in ("high", "medium")
    assert len(result["tool_trace"]) > 0
    # every cited evidence id must actually appear in the answer text
    for c in result["claims"]:
        for ev_id in c["evidence_ids"]:
            assert ev_id in result["answer"], f"claim cites {ev_id} but it's not in the answer text"


async def test_s2_bangalore_pricing_why_traces_to_slack(repo_id):
    result = await answer_question(
        "Why does the pricing code have a special case for Bangalore?", repo_id=repo_id
    )
    _assert_contract_shape(result)
    assert result["abstained"] is False
    tools_called = {t["tool"] for t in result["tool_trace"]}
    assert "explain_why" in tools_called or "search_code" in tools_called


async def test_unanswerable_question_produces_clean_abstention(repo_id):
    result = await answer_question(
        "What is Meridian's CEO's home address and personal phone number?", repo_id=repo_id
    )
    _assert_contract_shape(result)
    assert result["abstained"] is True
    assert result["claims"] == []
    assert result["confidence"] == "low"
    assert len(result["answer"]) > 0


def test_fast_path_only_engages_for_code_only_calls():
    """The plan call is skipped ONLY when every available tool is code-shaped.

    The moment a call also exposes accounts, tickets, Slack or a connector,
    choosing between sources is a real decision and the planner must run.
    """
    from app.agent.loop import _fast_path_calls

    live = {"search_code", "search_custom_docs", "explain_why",
            "trace_symbol", "find_usages", "read_file"}
    assert _fast_path_calls("what port does it listen on?", live) is not None
    # One non-code tool is enough to require planning.
    assert _fast_path_calls("q", live | {"get_account"}) is None
    assert _fast_path_calls("q", {"search_slack"}) is None
    assert _fast_path_calls("q", None) is None


def test_fast_path_pairs_explain_why_with_search_code():
    """A "why" question must never get explain_why ALONE — on its own it
    self-locates from the query text and explained the wrong file (the
    documented Bangalore regression). This mirrors the plan prompt's rule."""
    from app.agent.loop import _fast_path_calls

    live = {"search_code", "search_custom_docs", "explain_why"}
    why = [c["tool"] for c in _fast_path_calls("why does pricing special-case Bangalore?", live)]
    assert why == ["search_code", "explain_why"]

    plain = [c["tool"] for c in _fast_path_calls("what port does it listen on?", live)]
    assert "explain_why" not in plain
    assert plain[0] == "search_code"
