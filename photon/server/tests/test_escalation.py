"""Escalation rules — when the agent asks its person, and what it says.

The line that matters: escalate when the agent could not stand behind an
answer AND the question is worth a person's attention. Every spoken line is
also checked for the two things it must never do — pretend to BE the member,
or invent an answer when the member did not reply.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import escalation as rules  # noqa: E402

ABSTAINED = {"answer": "I don't have evidence for that.", "confidence": "low", "abstained": True}
CONFIDENT = {"answer": "It retries three times.", "confidence": "high", "abstained": False}


def test_topics():
    assert rules.topic_of("what would the enterprise plan cost for 40 seats?") == "pricing"
    assert rules.topic_of("when will SSO ship?") == "timeline"
    assert rules.topic_of("are you SOC 2 compliant") == "security"
    assert rules.topic_of("can you guarantee 99.9% uptime") == "commitment"
    assert rules.topic_of("why does the webhook return 401") == "technical"


def test_a_confident_answer_is_not_escalated():
    assert not rules.assess("how many times does it retry?", CONFIDENT).escalate


def test_an_important_abstention_is():
    a = rules.assess("what discount can you do on a two year contract?", ABSTAINED)
    assert a.escalate and a.topic == "pricing" and a.importance == "high"


def test_a_real_technical_question_is_escalated_at_medium():
    a = rules.assess("does the export API support filtering by date range?", ABSTAINED)
    assert a.escalate and a.importance == "medium"


def test_a_vague_fragment_is_not_worth_a_ping():
    assert not rules.assess("and that one", ABSTAINED).escalate


def test_the_members_own_list_wins_even_over_a_confident_answer():
    a = rules.assess("what does the pro plan cost", CONFIDENT, always_escalate=["pricing"])
    assert a.escalate and "yourself" in a.reason
    a = rules.assess("tell me about the Northwind rollout", CONFIDENT, always_escalate=["northwind"])
    assert a.escalate


def test_first_name():
    assert rules.first_name("Priya Nair") == "Priya"
    assert rules.first_name(None, "priya.nair@acme.com") == "Priya"
    assert rules.first_name(None, "86944435+rishik92@users.noreply.github.com") == "Rishik92"
    assert rules.first_name(None, None, None) == "the team"


def test_lines_speak_as_the_agent_never_as_the_member():
    for topic in ("pricing", "timeline", "security", "commitment", "technical"):
        for line in (rules.holding_line("Priya", topic), rules.apology_line("Priya", topic, "q")):
            assert "Priya" in line
            assert not line.lower().startswith("i'm priya")


def test_company_mode_names_the_team_not_a_person():
    assert "the team" in rules.holding_line("Priya", "pricing", company=True)
    assert "Priya" not in rules.apology_line("Priya", "timeline", "q", company=True)


def test_apology_never_carries_an_answer():
    """The member did not reply, so there is nothing to relay — the apology
    promises a follow-up and states no fact."""
    line = rules.apology_line("Priya", "pricing", "what does it cost?")
    assert "$" not in line and "writing" in line


def test_relay_credits_the_member():
    assert rules.relay_line("Priya", "yes, annual plans get 15% off") == \
        "Coming back to your earlier question — Priya says: yes, annual plans get 15% off."
