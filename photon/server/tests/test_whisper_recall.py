"""The Recall path, checked against Recall's documented payloads — no key.

The first version read `data.words`; Recall sends `data.data.words`. On a
real Meet call that parses to nothing, and whisper sits silent while the
transcript flows perfectly. These use the documented event shape verbatim.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routers.whisper import BOT_STATES, _bot_view, _check_meeting_url  # noqa: E402
from app.models import WhisperSession  # noqa: E402
from app.services.whisper.providers import lines_from_webhook  # noqa: E402


def _event(words, participant, event="transcript.data"):
    return {"event": event, "data": {
        "data": {"words": [{"text": w, "start_timestamp": {"relative": 1.0},
                            "end_timestamp": {"relative": 1.2}} for w in words],
                 "language_code": "en", "participant": participant},
        "realtime_endpoint": {"id": "re", "metadata": {}},
        "transcript": {"id": "t", "metadata": {}},
        "recording": {"id": "r", "metadata": {}},
        "bot": {"id": "b", "metadata": {"whisper_session_id": "s1"}},
    }}


def test_documented_transcript_event_is_parsed():
    lines = lines_from_webhook(_event(
        ["does", "it", "support", "SSO?"],
        {"id": 100, "name": "Dana Whitfield", "is_host": False, "platform": "google_meet",
         "extra_data": None, "email": "dana@client.com"}))
    assert lines == [{"speaker_name": "Dana Whitfield", "speaker_email": "dana@client.com",
                      "text": "does it support SSO?", "is_client": None}]


def test_partials_and_other_events_are_not_lines():
    assert lines_from_webhook(_event(["does", "it"], {"name": "Dana"},
                                     event="transcript.partial_data")) == []
    assert lines_from_webhook({"event": "participant_events.join",
                               "data": {"data": {"participant": {"name": "Dana"}}}}) == []


def test_host_is_us_and_unknown_stays_unresolved():
    host = lines_from_webhook(_event(["hello", "everyone"], {"name": "Priya", "is_host": True}))
    assert host[0]["is_client"] is False
    guest = lines_from_webhook(_event(["hello", "everyone"], {"name": "Dana", "is_host": None}))
    assert guest[0]["is_client"] is None      # resolved by name/email against members


def test_flat_shape_still_accepted_for_capture_scripts():
    flat = {"data": {"words": [{"text": "hi"}, {"text": "there"}],
                     "participant": {"name": "Dana", "extra_data": {"external": True}}}}
    assert lines_from_webhook(flat)[0]["is_client"] is True


def test_meeting_links():
    for url in ["https://meet.google.com/abc-defg-hij", "https://us02web.zoom.us/j/123",
                "https://teams.microsoft.com/l/meetup-join/x"]:
        assert _check_meeting_url(url) == url
    for url in ["http://meet.google.com/abc", "https://evil.com/meet.google.com",
                "https://meet.google.com.evil.com/x", "not a url"]:
        with pytest.raises(HTTPException):
            _check_meeting_url(url)


def test_waiting_room_tells_the_rep_what_to_do():
    w = WhisperSession(workspace_id="w", bot_status="in_waiting_room", external_ref="b")
    view = _bot_view(w, "Priya's Photon (notes)")
    assert view["label"] == "Waiting to be let in"
    assert "Admit “Priya's Photon (notes)”" in view["action"]
    assert _bot_view(WhisperSession(workspace_id="w", bot_status="in_call_recording"))["action"] is None
    assert all(label for label, _ in BOT_STATES.values())
