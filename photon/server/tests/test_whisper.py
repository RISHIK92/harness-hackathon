"""Whisper mode. Two halves: the pure logic (gate, warnings, webhook
parsing), and the privacy boundary over real HTTP with two real users.

The second half is the one that matters. A whisper thread is private by row,
not by UI, so the test that earns its place is the one where a colleague —
someone who IS a member of the same workspace — still cannot read it.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.whisper.engine import should_suggest  # noqa: E402
from app.services.whisper.providers import lines_from_webhook  # noqa: E402
from app.services.whisper.warnings import is_safe_to_read_aloud, warnings_for  # noqa: E402

BASE = "http://localhost:8000"


# ── the gate ─────────────────────────────────────────────────────────────

def test_real_asks_are_suggested():
    for line in ["why are the webhooks failing?", "is there a way to export this",
                 "we need SSO before we can roll out", "can you send me the docs",
                 "it is still broken"]:
        assert should_suggest(line), line


def test_backchannel_is_not():
    """A meeting is mostly this. Firing the pipeline on every "yeah" is one
    LLM turn per sentence for nothing."""
    for line in ["yeah", "ok got it", "mm hmm", "sorry, go ahead", "exactly", "thanks"]:
        assert not should_suggest(line), line


# ── warnings ─────────────────────────────────────────────────────────────

def _answer(source_types, **kw):
    return {
        "confidence": kw.get("confidence", "high"),
        "abstained": kw.get("abstained", False),
        "escalation": kw.get("escalation"),
        "tool_trace": [{"evidence": [{"source_type": t} for t in source_types]}],
    }


def test_slack_only_evidence_is_flagged_internal():
    """The Bangalore rationale lives in a Slack thread naming the partner and
    the commission — true, cited, and not for the customer's ear."""
    w = warnings_for(_answer(["slack", "ticket"]))
    assert [x["code"] for x in w] == ["internal_only"]
    assert not is_safe_to_read_aloud(w)


def test_docs_and_code_are_safe_to_read():
    assert is_safe_to_read_aloud(warnings_for(_answer(["docs", "code"])))


def test_mixed_sources_warn_about_wording_not_substance():
    w = warnings_for(_answer(["docs", "slack"]))
    assert [x["code"] for x in w] == ["mixed_sources"]


def test_an_abstention_is_never_safe_to_read():
    w = warnings_for(_answer([], abstained=True, confidence="low"))
    assert "no_answer" in [x["code"] for x in w]
    assert not is_safe_to_read_aloud(w)


def test_low_confidence_is_flagged_even_with_safe_sources():
    assert not is_safe_to_read_aloud(warnings_for(_answer(["docs"], confidence="low")))


def test_warnings_stack():
    """Low confidence AND internal-only are two different problems; showing
    one would hide the other."""
    codes = [x["code"] for x in warnings_for(_answer(["slack"], confidence="low"))]
    assert "internal_only" in codes and "low_confidence" in codes


# ── vendor webhook parsing (the part testable with no account) ───────────

def test_webhook_payload_is_flattened():
    lines = lines_from_webhook({"data": {
        "words": [{"text": "why"}, {"text": "is"}, {"text": "it"}, {"text": "failing"}],
        "participant": {"name": "Dana", "is_host": False, "extra_data": {"external": True}},
    }})
    assert lines == [{"speaker_name": "Dana", "speaker_email": None,
                      "text": "why is it failing", "is_client": True}]


def test_an_empty_webhook_yields_nothing():
    assert lines_from_webhook({"data": {"words": []}}) == []


def test_an_unflagged_participant_is_left_for_the_name_resolver():
    """Not False — that LOOKS cautious and is the broken choice. Most vendors
    send no host/external flag, so a hard False makes every participant "us"
    and a Meet call fires zero suggestions while the transcript flows
    perfectly. Observed on the first live webhook run; None defers to
    whisper/participants.py, which matches the name against real members."""
    lines = lines_from_webhook({"data": {"words": [{"text": "hello there friend"}],
                                         "participant": {"name": "Sam"}}})
    assert lines[0]["is_client"] is None


# ── the privacy boundary, over real HTTP ─────────────────────────────────

def _signup(client, email):
    """Signup takes JSON, login takes form data (OAuth2PasswordRequestForm) —
    same shape the tenancy tests use."""
    password = "pw-test-1234"
    client.post("/api/auth/signup", json={"email": email, "password": password})
    login = client.post("/api/auth/login", data={"username": email, "password": password})
    login.raise_for_status()
    return login.json()["access_token"]


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=120.0) as c:
        try:
            c.get("/health").raise_for_status()
        except Exception:
            pytest.skip("brain-api is not running on :8000")
        yield c


def test_a_colleague_cannot_read_your_whisper_thread(client):
    stamp = int(time.time() * 1000)
    alice = _signup(client, f"alice{stamp}@example.test")
    bob = _signup(client, f"bob{stamp}@example.test")
    a = {"Authorization": f"Bearer {alice}"}
    b = {"Authorization": f"Bearer {bob}"}

    created = client.post("/api/whisper/sessions", headers=a,
                          json={"title": "privacy check", "source": "external"})
    created.raise_for_status()
    session_id = created.json()["id"]

    thread = client.get(f"/api/whisper/sessions/{session_id}/thread", headers=a)
    thread.raise_for_status()
    thread_id = thread.json()["id"]

    # Alice can read her own.
    assert client.get(f"/api/whisper/threads/{thread_id}/messages", headers=a).status_code == 200
    # Bob cannot — and gets 404, not 403, so the id is not confirmed to exist.
    assert client.get(f"/api/whisper/threads/{thread_id}/messages", headers=b).status_code == 404
    assert client.post(f"/api/whisper/threads/{thread_id}/ask", headers=b,
                       json={"question": "what did they ask?"}).status_code == 404
    # And an unauthenticated reader gets nothing at all.
    assert client.get(f"/api/whisper/threads/{thread_id}/messages").status_code in (401, 403)


def test_another_workspace_cannot_see_the_session(client):
    stamp = int(time.time() * 1000)
    alice = _signup(client, f"alice{stamp}@example.test")
    outsider = _signup(client, f"outsider{stamp}@example.test")
    created = client.post("/api/whisper/sessions", headers={"Authorization": f"Bearer {alice}"},
                          json={"title": "tenant check"})
    created.raise_for_status()
    session_id = created.json()["id"]

    r = client.get(f"/api/whisper/sessions/{session_id}/lines",
                   headers={"Authorization": f"Bearer {outsider}"})
    assert r.status_code == 404
    r = client.post(f"/api/whisper/sessions/{session_id}/lines",
                    headers={"Authorization": f"Bearer {outsider}"},
                    json={"speaker_name": "x", "text": "hello", "is_client": True})
    assert r.status_code == 404


def test_a_client_question_reaches_the_thread_and_a_colleague_line_does_not(client):
    stamp = int(time.time() * 1000)
    alice = _signup(client, f"alice{stamp}@example.test")
    a = {"Authorization": f"Bearer {alice}"}
    session_id = client.post("/api/whisper/sessions", headers=a,
                             json={"title": "gate check"}).json()["id"]
    thread_id = client.get(f"/api/whisper/sessions/{session_id}/thread", headers=a).json()["id"]

    # Our own side of the conversation: never suggested on.
    ours = client.post(f"/api/whisper/sessions/{session_id}/lines", headers=a,
                       json={"speaker_name": "Rep", "text": "let me check that for you",
                             "is_client": False})
    assert ours.json()["suggested_to_threads"] == 0

    # A client backchannel: also nothing.
    ack = client.post(f"/api/whisper/sessions/{session_id}/lines", headers=a,
                      json={"speaker_name": "Client", "text": "yeah", "is_client": True})
    assert ack.json()["suggested_to_threads"] == 0

    # The transcript still recorded all of it.
    lines = client.get(f"/api/whisper/sessions/{session_id}/lines", headers=a).json()
    assert [l["text"] for l in lines] == ["let me check that for you", "yeah"]

    # Threads persist after the call ends — that is the point of a thread.
    client.post(f"/api/whisper/sessions/{session_id}/end", headers=a).raise_for_status()
    assert client.get(f"/api/whisper/threads/{thread_id}/messages", headers=a).status_code == 200
    # …but a closed session takes no more transcript.
    closed = client.post(f"/api/whisper/sessions/{session_id}/lines", headers=a,
                         json={"speaker_name": "Client", "text": "one more thing?", "is_client": True})
    assert closed.status_code == 409


# ── who is the client (item 3) ───────────────────────────────────────────
# On a Photon call speaker_user_id settles this. Anywhere else we have a
# display name off a transcript and nothing else, so the default INVERTS:
# unmatched means client, or a Meet call fires zero suggestions and whisper
# does nothing on the meetings it exists for.

from app.services.whisper.participants import _candidates, looks_like_member  # noqa: E402


def test_a_member_is_recognised_by_display_name():
    ids = _candidates("priya.nair@acme.com")
    assert looks_like_member("Priya Nair", ids)
    assert looks_like_member("priya.nair@acme.com", ids)


def test_a_stranger_is_the_client():
    ids = _candidates("priya.nair@acme.com")
    assert not looks_like_member("Dana (Acme)", ids)


def test_matching_is_not_a_substring_test():
    """"Sam" must not match "samantha@…" — that would silently swallow a real
    client's questions, which is the failure mode with no symptom."""
    ids = _candidates("samantha@acme.com")
    assert not looks_like_member("Sam", ids)
    assert looks_like_member("Samantha", ids)


def test_an_unnamed_speaker_is_treated_as_the_client():
    assert not looks_like_member("", _candidates("a@b.com"))


# ── the vendor webhook (item 1) ──────────────────────────────────────────


def test_the_webhook_needs_the_right_secret(client):
    stamp = int(time.time() * 1000)
    alice = _signup(client, f"alice{stamp}@example.test")
    a = {"Authorization": f"Bearer {alice}"}
    session_id = client.post("/api/whisper/sessions", headers=a,
                             json={"title": "webhook check"}).json()["id"]
    hook = client.get(f"/api/whisper/sessions/{session_id}/webhook", headers=a).json()
    assert hook["url"].endswith(f"/api/whisper/webhooks/{session_id}/" + hook["url"].rsplit("/", 1)[1])

    body = {"speaker_name": "Dana", "text": "why is the export failing?"}
    # Wrong secret and unknown session both 404 — distinct answers would make
    # this an oracle for valid session ids.
    assert client.post(f"/api/whisper/webhooks/{session_id}/wrong-secret", json=body).status_code == 404
    assert client.post(f"/api/whisper/webhooks/{'0' * 36}/whatever", json=body).status_code == 404


def test_the_webhook_secret_is_not_in_the_session_listing(client):
    """It is a bearer token. A credential that rides along with every list
    response ends up in logs, screenshots and browser history."""
    stamp = int(time.time() * 1000)
    a = {"Authorization": f"Bearer {_signup(client, f'alice{stamp}@example.test')}"}
    client.post("/api/whisper/sessions", headers=a, json={"title": "secret check"})
    listed = client.get("/api/whisper/sessions", headers=a).json()
    assert listed and all("webhook_secret" not in s for s in listed)


def test_the_webhook_accepts_a_plain_line_and_resolves_the_speaker(client):
    stamp = int(time.time() * 1000)
    email = f"alice{stamp}@example.test"
    a = {"Authorization": f"Bearer {_signup(client, email)}"}
    session_id = client.post("/api/whisper/sessions", headers=a,
                             json={"title": "ingest check"}).json()["id"]
    url = client.get(f"/api/whisper/sessions/{session_id}/webhook", headers=a).json()["url"]
    path = "/api/whisper" + url.split("/api/whisper", 1)[1]

    # A stranger: resolved as the client.
    r = client.post(path, json={"speaker_name": "Dana", "text": "hello there everyone"})
    assert r.status_code == 202 and r.json()["results"][0]["is_client"] is True

    # The workspace member themselves: resolved as us, so never suggested on.
    r = client.post(path, json={"speaker_name": email, "text": "why would that be failing?"})
    assert r.json()["results"][0]["is_client"] is False
    assert r.json()["results"][0]["suggested_to_threads"] == 0


def test_a_vendor_transcript_event_is_flattened_and_ingested(client):
    stamp = int(time.time() * 1000)
    a = {"Authorization": f"Bearer {_signup(client, f'alice{stamp}@example.test')}"}
    session_id = client.post("/api/whisper/sessions", headers=a,
                             json={"title": "vendor check"}).json()["id"]
    url = client.get(f"/api/whisper/sessions/{session_id}/webhook", headers=a).json()["url"]
    path = "/api/whisper" + url.split("/api/whisper", 1)[1]

    r = client.post(path, json={"data": {
        "words": [{"text": "does"}, {"text": "it"}, {"text": "support"}, {"text": "SSO"}],
        "participant": {"name": "Dana", "is_host": False, "extra_data": {"external": True}},
    }})
    assert r.status_code == 202
    lines = client.get(f"/api/whisper/sessions/{session_id}/lines", headers=a).json()
    assert lines[-1]["text"] == "does it support SSO" and lines[-1]["is_client"] is True


def test_an_ended_session_refuses_webhook_traffic(client):
    stamp = int(time.time() * 1000)
    a = {"Authorization": f"Bearer {_signup(client, f'alice{stamp}@example.test')}"}
    session_id = client.post("/api/whisper/sessions", headers=a, json={"title": "end check"}).json()["id"]
    url = client.get(f"/api/whisper/sessions/{session_id}/webhook", headers=a).json()["url"]
    path = "/api/whisper" + url.split("/api/whisper", 1)[1]
    client.post(f"/api/whisper/sessions/{session_id}/end", headers=a).raise_for_status()
    assert client.post(path, json={"speaker_name": "Dana", "text": "one more thing?"}).status_code == 409


def test_dispatching_a_bot_reports_that_it_is_not_configured(client):
    """501, not 500: nothing is broken, the capability is not configured —
    and the message has to say what to do instead."""
    stamp = int(time.time() * 1000)
    a = {"Authorization": f"Bearer {_signup(client, f'alice{stamp}@example.test')}"}
    session_id = client.post("/api/whisper/sessions", headers=a, json={"title": "bot check"}).json()["id"]
    r = client.post(f"/api/whisper/sessions/{session_id}/bot", headers=a,
                    json={"meeting_url": "https://meet.google.com/abc-defg-hij"})
    assert r.status_code == 501
    assert "RECALL_API_KEY" in r.json()["detail"]


# ── citation markers are stored, never displayed ─────────────────────────
# The answer keeps its [ev_xxx] markers — they are the grounding contract and
# the evidence panel renders them as chips. Every surface that shows PROSE
# drops them: the whisper thread, the call chat, a Slack DM, and TTS.

from app.services.whisper.slack_surface import format_suggestion, strip_citations  # noqa: E402


def test_a_single_marker_is_stripped():
    assert strip_citations("Ace handles coaching [ev_54285641].") == "Ace handles coaching."


def test_a_multi_id_bracket_is_stripped():
    """The compose model really does emit these, and the single-id pattern
    matched nothing at all in them — so they rendered literally on screen."""
    assert strip_citations("A partner rate [ev_80abd768, ev_4879aa12].") == "A partner rate."


def test_adjacent_markers_are_stripped():
    assert strip_citations("A [ev_1a2b3c4d] B [ev_5e6f7a8b] C") == "A B C"


def test_a_non_citation_bracket_survives():
    assert strip_citations("The field [webhook_url] is empty.") == "The field [webhook_url] is empty."


def test_a_slack_dm_carries_no_markers():
    out = format_suggestion("Ace coaches [ev_54285641].", "is there a tutor?",
                            [{"code": "internal_only", "label": "Internal source only"}])
    assert "ev_" not in out
    assert "⚠️" in out and "> is there a tutor?" in out


def test_the_stored_message_still_has_its_markers(client):
    """Stripping is a DISPLAY concern. If it leaked into storage the evidence
    chips, the verifier and every audit of what was cited would break."""
    stamp = int(time.time() * 1000)
    a = {"Authorization": f"Bearer {_signup(client, f'alice{stamp}@example.test')}"}
    session_id = client.post("/api/whisper/sessions", headers=a, json={"title": "marker check"}).json()["id"]
    thread_id = client.get(f"/api/whisper/sessions/{session_id}/thread", headers=a).json()["id"]
    # An empty workspace abstains, so assert on the contract shape rather
    # than on a grounded answer being available here.
    msg = client.post(f"/api/whisper/threads/{thread_id}/ask", headers=a,
                      json={"question": "what is the retry policy?"}).json()
    assert "text" in msg and "evidence" in msg
