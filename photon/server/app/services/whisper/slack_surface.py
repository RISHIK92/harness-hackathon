"""Whisper delivered as a Slack DM instead of a web thread.

The reason this exists: whisper competes for the rep's attention with the
call itself. A second browser tab is one glance too many, and the people who
would use this already have Slack open on the other monitor.

It is the same privacy model, not a weaker one — a DM to the member's own
Slack user id, never a channel, so a suggestion built from internal sources
cannot land somewhere the customer's guest account can read.

**Requires a scope this app does not currently request.** The Slack
connection is deliberately read-only (`channels:read/history`, `groups:*`,
`users:read`, `team:read` — see routers/slack.py), which is a property a
reviewer approving the app can check: Photon cannot post to your Slack. DMing
a suggestion needs `chat:write`, so enabling this is a real change to what
the app can do and has to be a deliberate re-install, not a silent upgrade.
Until then this reports exactly that rather than failing obscurely.
"""
from __future__ import annotations

import re

import httpx
import structlog
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.crypto import decrypt
from app.models import SlackInstallation, User

log = structlog.get_logger()

REQUIRED_SCOPE = "chat:write"
_TIMEOUT = 20.0


class SlackSurfaceUnavailable(RuntimeError):
    pass


async def _installation(session: AsyncSession, workspace_id: str) -> SlackInstallation:
    install = (await session.execute(
        select(SlackInstallation).where(SlackInstallation.workspace_id == workspace_id)
    )).scalars().first()
    if not install or not install.bot_token_encrypted:
        raise SlackSurfaceUnavailable("no Slack connected for this workspace")
    return install


def _slack_user_id(token: str, email: str) -> str:
    """Map a Photon account to a Slack account by verified email.

    By email rather than by name: two people share a display name far more
    often than an email, and whispering one rep's private thread to another
    is the exact failure this module must not have.
    """
    response = httpx.get(
        "https://slack.com/api/users.lookupByEmail",
        params={"email": email},
        headers={"Authorization": f"Bearer {token}"},
        timeout=_TIMEOUT,
    )
    data = response.json()
    if not data.get("ok"):
        raise SlackSurfaceUnavailable(f"Slack could not find {email}: {data.get('error')}")
    return data["user"]["id"]


# Same split the browser and the TTS make: the stored answer keeps its
# markers (they are the grounding contract), and every surface that shows
# PROSE drops them. A Slack DM has nowhere to render a citation chip, so a
# raw "[ev_54285641]" there is just noise in the middle of a sentence the rep
# is about to read to a customer.
_CITATION = re.compile(r"\s*\[\s*ev_[0-9a-f]+(?:\s*,\s*ev_[0-9a-f]+)*\s*\]")


def strip_citations(text: str) -> str:
    stripped = _CITATION.sub("", text or "")
    stripped = re.sub(r"\s+([,.;:!?])", r"\1", stripped)
    return re.sub(r"[ \t]{2,}", " ", stripped).strip()


def format_suggestion(text: str, trigger: str | None, warnings: list[dict]) -> str:
    """Slack markup. Warnings go FIRST — they are the reason to read on, and
    a caution below the fold is a caution nobody sees."""
    parts = []
    if warnings:
        parts.append(" ".join(f"⚠️ *{w['label']}*" for w in warnings))
    if trigger:
        parts.append(f"> {trigger}")
    parts.append(strip_citations(text))
    return "\n".join(parts)


async def send_suggestion(
    session: AsyncSession, workspace_id: str, user: User, text: str,
    trigger: str | None, warnings: list[dict],
) -> str:
    install = await _installation(session, workspace_id)
    token = decrypt(install.bot_token_encrypted)
    if not token:
        raise SlackSurfaceUnavailable("stored Slack token could not be decrypted — reconnect Slack")

    slack_user = _slack_user_id(token, user.email)
    response = httpx.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"channel": slack_user, "text": format_suggestion(text, trigger, warnings)},
        timeout=_TIMEOUT,
    )
    data = response.json()
    if not data.get("ok"):
        if data.get("error") in ("missing_scope", "not_allowed_token_type"):
            raise SlackSurfaceUnavailable(
                f"the Slack app needs the {REQUIRED_SCOPE} scope to DM suggestions — "
                "it is currently read-only, so this needs a re-install with that scope added"
            )
        raise SlackSurfaceUnavailable(f"Slack rejected the message: {data.get('error')}")
    log.info("whisper.slack_sent", workspace_id=workspace_id, user=user.email)
    return data.get("ts", "")
