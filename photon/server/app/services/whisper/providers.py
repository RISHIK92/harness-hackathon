"""Getting a transcript out of a meeting we don't own.

Three ways in, one shape out — every one of them ends up POSTing lines to
`/api/whisper/sessions/{id}/lines`, which is what keeps whisper from being
welded to a single vendor.

  PHOTON    Photon's own call. Already transcribes every turn, so this needs
            nothing external and works today (routers/meetings.py feeds it).
  EXTERNAL  Anything else POSTing lines: a browser extension, a desktop
            capture, a vendor webhook you wire up yourself.
  BOT       A meeting-bot vendor joins Meet / Teams / Zoom on our behalf and
            streams the transcript back.

Only the third needs an account, and it is the honest reason Section D's
first item cannot be finished here: joining Zoom or Teams means either a
vendor (Recall.ai and friends) or per-platform SDKs and an approved app, and
a webhook URL the vendor can reach — this deployment binds to localhost.

The adapter below is written against Recall.ai's documented API because it
is the one that covers all three platforms with one integration. The request
and payload shapes are checked against Recall's current docs (bot create,
real-time event payloads); a live bot run still needs a key and a public URL.
It fails loudly without them rather than pretending.
"""
from __future__ import annotations

import httpx
import structlog

from app.config import get_settings

log = structlog.get_logger()

_TIMEOUT = 30.0


class BotProviderUnavailable(RuntimeError):
    """No bot vendor is configured. Raised rather than returning a fake id:
    a session that silently never receives a transcript looks exactly like a
    meeting where nobody spoke."""


def is_configured() -> bool:
    return bool(getattr(get_settings(), "recall_api_key", ""))


def _headers() -> dict:
    settings = get_settings()
    return {
        "Authorization": f"Token {settings.recall_api_key}",
        "Content-Type": "application/json",
    }


def send_bot(meeting_url: str, webhook_url: str, bot_name: str = "Photon",
             announce: str | None = None, metadata: dict | None = None) -> str:
    """Ask the vendor to join `meeting_url` and stream transcript to us.

    Returns the vendor's bot id, which becomes the session's `external_ref`.

    `bot_name` is what the client sees in the participant list — which is
    the honest limit of this path: a bot that joins a Meet call IS visible
    to everyone in it. `announce` is posted to the meeting chat on join, so
    the people being transcribed are told so by the thing transcribing them.
    """
    settings = get_settings()
    if not is_configured():
        raise BotProviderUnavailable(
            "no meeting-bot vendor configured — set RECALL_API_KEY, or POST transcript "
            "lines directly to /api/whisper/sessions/{id}/lines"
        )
    body = {
        "meeting_url": meeting_url,
        "bot_name": bot_name[:100],
        "metadata": {k: str(v) for k, v in (metadata or {}).items()},
        "recording_config": {
            # Real-time, not the post-call artefact: a suggestion that
            # arrives after the call is a meeting summary, not a whisper.
            # recallai_streaming rather than the platform's own captions: it
            # works on every platform without the host turning captions on,
            # and low-latency mode is the one that matters mid-conversation.
            "transcript": {
                "provider": {settings.recall_transcript_provider: (
                    {"mode": "prioritize_low_latency", "language_code": "en"}
                    if settings.recall_transcript_provider == "recallai_streaming" else {}
                )},
                # Per-participant audio where the platform offers it, so a
                # line is attributed to who actually said it — the suggestion
                # gate hangs on that attribution.
                "diarization": {"use_separate_streams_when_available": True},
            },
            "realtime_endpoints": [
                {"type": "webhook", "url": webhook_url, "events": ["transcript.data"]}
            ],
        },
        # A bot parked in a waiting room for the default 20 minutes is a bot
        # nobody noticed; ten is long enough for a host to admit it.
        "automatic_leave": {"waiting_room_timeout": 600, "noone_joined_timeout": 600},
    }
    if announce:
        body["chat"] = {"on_bot_join": {"send_to": "everyone", "message": announce[:500]}}
    response = httpx.post(
        f"{settings.recall_api_base}/api/v1/bot/", headers=_headers(), json=body, timeout=_TIMEOUT
    )
    response.raise_for_status()
    bot_id = response.json().get("id")
    log.info("whisper.bot_dispatched", bot_id=bot_id)
    return bot_id


def bot_status(bot_id: str) -> str | None:
    """The bot's latest status code, straight from the vendor.

    Polled rather than pushed: Recall's status webhooks are configured
    account-wide in its dashboard, while this needs nothing but the key.
    """
    if not is_configured():
        raise BotProviderUnavailable("no meeting-bot vendor configured")
    settings = get_settings()
    response = httpx.get(f"{settings.recall_api_base}/api/v1/bot/{bot_id}/",
                         headers=_headers(), timeout=_TIMEOUT)
    response.raise_for_status()
    changes = response.json().get("status_changes") or []
    return changes[-1].get("code") if changes else None


def stop_bot(bot_id: str) -> None:
    if not is_configured():
        raise BotProviderUnavailable("no meeting-bot vendor configured")
    settings = get_settings()
    response = httpx.post(
        f"{settings.recall_api_base}/api/v1/bot/{bot_id}/leave_call/",
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    log.info("whisper.bot_stopped", bot_id=bot_id)


def lines_from_webhook(payload: dict) -> list[dict]:
    """Flatten a vendor transcript webhook into our own line shape.

    Kept as a pure function precisely because the live path cannot be tested
    here: the parsing is the part that will be wrong, and it can be checked
    against a recorded payload with no account at all.

    `is_client` is None whenever the vendor gives no reliable signal, which
    hands the decision to whisper/participants.py to resolve from the speaker
    name against the workspace's members. An explicit False here would LOOK
    like the cautious choice and is in fact the broken one: most vendors send
    no host/external flag at all, so every participant would come back as
    "us" and a Meet call would fire zero suggestions — silently, with the
    transcript flowing perfectly. Observed exactly that on the first live
    webhook run.
    """
    # Recall nests the transcript one level down — event.data.data.words —
    # with bot/recording/transcript ids alongside it. A parser reading
    # data.words finds nothing on a real event and whisper goes silent with
    # the transcript flowing perfectly; the flat shape is still accepted
    # for hand-rolled capture scripts that post it.
    if payload.get("event") and payload["event"] != "transcript.data":
        return []                       # partials, participant events: not a line yet
    outer = payload.get("data") or {}
    data = outer.get("data") if isinstance(outer.get("data"), dict) else outer
    words = data.get("words") or data.get("transcript") or []
    participant = data.get("participant") or {}
    text = " ".join(w.get("text", "") for w in words).strip() if isinstance(words, list) else str(words)
    if not text:
        return []
    is_client = None
    extra = participant.get("extra_data") or {}
    if extra.get("external") is not None:
        is_client = bool(extra["external"])
    elif participant.get("is_host") is True:
        # A host is running the meeting; on a call we booked that is us.
        # Absence of the flag says nothing, so it stays None.
        is_client = False
    return [{
        "speaker_name": participant.get("name") or "Unknown",
        "speaker_email": participant.get("email"),
        "text": text,
        "is_client": is_client,
    }]
