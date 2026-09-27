"""Commitments heard on a call — what counts, and what the draft says.

A false positive costs a draft the member discards with one click; a false
negative is a promise to a customer that nobody tracked. So the detector is
permissive about phrasing and strict about only one thing: hedged or
questioning sentences are not commitments.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import commitments as c  # noqa: E402


def test_detects_fix_commitments_with_a_due_date():
    found = c.detect("Yeah, that's on us. We'll fix the export timeout by Friday.")
    assert found and found.sentence == "We'll fix the export timeout by Friday."
    assert found.due == "friday"


def test_other_phrasings():
    for line in ["I'll look into why the webhook keeps failing.",
                 "Let me investigate that retry issue this week.",
                 "We're going to patch that before the next release.",
                 "we can sort out the CSV encoding by tomorrow"]:
        assert c.detect(line), line


def test_hedges_questions_and_non_fix_promises_are_not_commitments():
    for line in ["Maybe we'll fix that at some point.",
                 "We can't fix that on the current plan.",
                 "Will you fix that by Friday?",
                 "I'll send you the deck after the call.",
                 "The fix shipped last week."]:
        assert c.detect(line) is None, line


def test_title_prefers_the_clients_problem():
    found = c.detect("We'll fix that by Friday.")
    title = c.title_for(found, ["hi", "the export times out on accounts with 10k rows"])
    assert title == "the export times out on accounts with 10k rows"
    assert c.title_for(found, []) == "We'll fix that by Friday"


def test_issue_text_carries_context_and_the_promise():
    found = c.detect("We'll fix that by Friday.")
    text = c.issue_text_for(found, ["exports time out on big accounts"], "Acme sync", "Priya")
    assert "> exports time out on big accounts" in text
    assert 'Priya committed: "We\'ll fix that by Friday."' in text
    assert "Promised by: friday" in text
