"""Slack: connect a workspace, choose channels, keep the bot token safe.

Workspace-scoped by design (see the source catalog): a Slack connection is
shared, so anyone in the Photon workspace can get answers drawn from it.
That is why connecting is an OWNER action — it widens what every member,
and every guest on a call, can learn.
"""
from __future__ import annotations

import secrets
from datetime import datetime
from typing import Optional

import httpx
import redis
import structlog
import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.core.auth import get_current_user
from app.core.crypto import decrypt, encrypt
from app.core.workspace import get_current_workspace, require_role
from app.database import get_session
from app.models import (
    SlackChannel,
    SlackInstallation,
    SlackInstallationRead,
    User,
    Workspace,
    WorkspaceRole,
)

log = structlog.get_logger()
router = APIRouter()
settings = get_settings()

_INSTALL_NONCE_TTL_SECONDS = 600
_redis_client: Optional[redis.Redis] = None

# Read-only, and only what the tools actually need: channel list, message
# history, and user names to attribute a message to a person. No write
# scopes at all — the agent never posts to Slack.
SLACK_SCOPES = "channels:read,channels:history,groups:read,groups:history,users:read,team:read"


def _redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.redis_url)
    return _redis_client


def _require_configured() -> None:
    if not settings.slack_client_id or not settings.slack_client_secret:
        raise HTTPException(
            status_code=503,
            detail="Slack is not configured on this deployment — create the app at /dev/slack-app/new first",
        )


@router.post("/connect")
async def start_install(
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(require_role(WorkspaceRole.OWNER)),
):
    """Begin OAuth. The nonce binds the callback to THIS workspace, so a
    stray callback cannot attach someone's Slack to the wrong tenant."""
    _require_configured()
    nonce = secrets.token_urlsafe(24)
    _redis().set(f"slack:install_nonce:{nonce}", f"{workspace.id}:{current_user.id}", ex=_INSTALL_NONCE_TTL_SECONDS)
    redirect_uri = f"{settings.public_base_url}/api/integrations/slack/callback"
    url = (
        "https://slack.com/oauth/v2/authorize"
        f"?client_id={settings.slack_client_id}"
        f"&scope={SLACK_SCOPES}"
        f"&redirect_uri={redirect_uri}"
        f"&state={nonce}"
    )
    return {"url": url}


@router.get("/callback")
async def oauth_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    session: AsyncSession = Depends(get_session),
):
    if error:
        # The user pressed Cancel on Slack's consent screen. Not an error
        # worth a stack trace — send them back where they came from.
        return RedirectResponse(f"{settings.client_base_url}/dashboard?slack=cancelled")
    _require_configured()

    raw = _redis().get(f"slack:install_nonce:{state}") if state else None
    if not raw:
        raise HTTPException(status_code=400, detail="Expired or invalid Slack connection attempt — try again")
    _redis().delete(f"slack:install_nonce:{state}")
    workspace_id, user_id = (raw.decode() if isinstance(raw, bytes) else raw).split(":", 1)

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": settings.slack_client_id,
                "client_secret": settings.slack_client_secret,
                "code": code,
                "redirect_uri": f"{settings.public_base_url}/api/integrations/slack/callback",
            },
        )
    payload = resp.json()
    # Slack returns HTTP 200 with {"ok": false} on failure, so status code
    # alone would look like success.
    if not payload.get("ok"):
        log.error("slack.oauth_failed", error=payload.get("error"))
        raise HTTPException(status_code=502, detail=f"Slack rejected the connection: {payload.get('error')}")

    team = payload.get("team") or {}
    bot_token = payload.get("access_token", "")

    result = await session.execute(
        select(SlackInstallation).where(
            SlackInstallation.workspace_id == workspace_id,
            SlackInstallation.team_id == team.get("id", ""),
        )
    )
    install = result.scalars().first()
    if install:
        install.bot_token_encrypted = encrypt(bot_token)
        install.team_name = team.get("name", install.team_name)
    else:
        install = SlackInstallation(
            workspace_id=workspace_id,
            team_id=team.get("id", ""),
            team_name=team.get("name", "unknown"),
            bot_token_encrypted=encrypt(bot_token),
            bot_user_id=payload.get("bot_user_id"),
            installed_by=user_id,
        )
    session.add(install)
    await session.commit()

    log.info("slack.connected", team=team.get("name"), workspace_id=workspace_id)
    return RedirectResponse(f"{settings.client_base_url}/dashboard?slack=connected")


@router.get("", response_model=list[SlackInstallationRead])
async def list_installations(
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
):
    result = await session.execute(
        select(SlackInstallation).where(SlackInstallation.workspace_id == workspace.id)
    )
    return result.scalars().all()


async def token_for(session: AsyncSession, installation: SlackInstallation) -> str:
    token = decrypt(installation.bot_token_encrypted)
    if not token:
        # Only happens if secret_key rotated since the token was stored.
        raise HTTPException(
            status_code=409,
            detail="This Slack connection can no longer be decrypted — reconnect Slack",
        )
    return token


@router.get("/{installation_id}/channels")
async def list_channels(
    installation_id: str,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
):
    """Channels the bot can see, marked with what is already selected.

    Only channels the app has been ADDED to are readable — Slack enforces
    that, and it is the right default: connecting Slack should not hand us
    every conversation in the company.
    """
    install = await session.get(SlackInstallation, installation_id)
    if not install or install.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Slack connection not found")
    token = await token_for(session, install)

    channels: list[dict] = []
    cursor = ""
    async with httpx.AsyncClient(timeout=20.0) as client:
        while True:
            resp = await client.get(
                "https://slack.com/api/conversations.list",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "types": "public_channel,private_channel",
                    "limit": 200,
                    "exclude_archived": "true",
                    **({"cursor": cursor} if cursor else {}),
                },
            )
            data = resp.json()
            if not data.get("ok"):
                raise HTTPException(status_code=502, detail=f"Slack error: {data.get('error')}")
            channels.extend(data.get("channels", []))
            cursor = (data.get("response_metadata") or {}).get("next_cursor", "")
            if not cursor:
                break

    selected = await session.execute(
        select(SlackChannel.channel_id).where(
            SlackChannel.installation_id == install.id, SlackChannel.selected == True  # noqa: E712
        )
    )
    selected_ids = {c for (c,) in selected.all()}

    return {
        "installation": SlackInstallationRead.model_validate(install),
        "channels": [
            {
                "id": c["id"],
                "name": c.get("name", ""),
                "is_private": c.get("is_private", False),
                "is_member": c.get("is_member", False),
                "selected": c["id"] in selected_ids,
            }
            for c in channels
        ],
    }


class SelectChannelsBody(SQLModel):
    channel_ids: list[str] = []


@router.post("/{installation_id}/channels")
async def select_channels(
    installation_id: str,
    payload: SelectChannelsBody,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(require_role(WorkspaceRole.MEMBER)),
):
    """Replace the selection. Deselecting stops future syncs of a channel;
    it does not retroactively delete what was already indexed — that needs
    a separate, explicit purge so nobody loses history by mis-clicking."""
    install = await session.get(SlackInstallation, installation_id)
    if not install or install.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Slack connection not found")
    token = await token_for(session, install)

    existing = await session.execute(
        select(SlackChannel).where(SlackChannel.installation_id == install.id)
    )
    by_id = {c.channel_id: c for c in existing.scalars().all()}

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            "https://slack.com/api/conversations.list",
            headers={"Authorization": f"Bearer {token}"},
            params={"types": "public_channel,private_channel", "limit": 200, "exclude_archived": "true"},
        )
        names = {c["id"]: c for c in resp.json().get("channels", [])}

    for channel_id in payload.channel_ids:
        row = by_id.get(channel_id)
        meta = names.get(channel_id, {})
        if row:
            row.selected = True
        else:
            row = SlackChannel(
                installation_id=install.id,
                workspace_id=workspace.id,
                channel_id=channel_id,
                name=meta.get("name", channel_id),
                is_private=meta.get("is_private", False),
            )
        session.add(row)
    for channel_id, row in by_id.items():
        if channel_id not in payload.channel_ids:
            row.selected = False
            session.add(row)

    await session.commit()

    # Selecting channels is only useful once they are actually pulled in,
    # so the sync is queued here rather than behind a second button nobody
    # would find.
    from app.tasks.slack_ingest import sync_slack

    sync_slack.apply_async(args=[install.id])
    return {"selected": payload.channel_ids, "syncing": True}


@router.post("/{installation_id}/sync")
async def resync(
    installation_id: str,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(require_role(WorkspaceRole.MEMBER)),
):
    install = await session.get(SlackInstallation, installation_id)
    if not install or install.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Slack connection not found")
    from app.tasks.slack_ingest import sync_slack

    sync_slack.apply_async(args=[install.id])
    return {"syncing": True}


# ── Import a Slack export ────────────────────────────────────────────────
# The path that needs no Slack app at all. See services/slack_export.py for
# why this exists alongside OAuth rather than instead of it.

# Slack exports of a large workspace get big; this is a guard against
# filling the disk, not a judgement about what is reasonable to import.
_MAX_EXPORT_BYTES = 512 * 1024 * 1024


async def _save_upload(file: UploadFile) -> str:
    fd, path = tempfile.mkstemp(suffix=".zip")
    written = 0
    with os.fdopen(fd, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > _MAX_EXPORT_BYTES:
                out.close()
                os.unlink(path)
                raise HTTPException(status_code=413, detail="That export is larger than 512 MB")
            out.write(chunk)
    return path


@router.post("/export/inspect")
async def inspect_slack_export(
    file: UploadFile = File(...),
    workspace: Workspace = Depends(require_role(WorkspaceRole.MEMBER)),
):
    """What's inside the zip, before anything is indexed."""
    from app.services.slack_export import inspect_export

    path = await _save_upload(file)
    try:
        summary = inspect_export(path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not read that as a Slack export: {exc}")
    finally:
        os.unlink(path)

    if not summary.get("looks_like_slack_export"):
        raise HTTPException(
            status_code=400,
            detail="That zip doesn't look like a Slack export (no users.json or channel folders)",
        )
    return summary


@router.post("/export/import")
async def import_slack_export(
    file: UploadFile = File(...),
    channels: str = Form(default=""),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(require_role(WorkspaceRole.MEMBER)),
):
    """Index selected channels from an export.

    Channels are explicit for the same reason as the OAuth path: importing
    everything would quietly embed #random and every standup, and the cost
    and the privacy exposure are both real.
    """
    from app.services.slack_export import import_export

    wanted = {c.strip() for c in channels.split(",") if c.strip()} or None
    path = await _save_upload(file)
    try:
        counts = import_export(workspace.id, path, wanted)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Import failed: {exc}")
    finally:
        os.unlink(path)

    # Recorded as an installation so the UI can show "Slack: connected"
    # uniformly, whether it arrived by OAuth or by export.
    result = await session.execute(
        select(SlackInstallation).where(
            SlackInstallation.workspace_id == workspace.id,
            SlackInstallation.team_id == "export",
        )
    )
    install = result.scalars().first()
    if not install:
        install = SlackInstallation(
            workspace_id=workspace.id,
            team_id="export",
            team_name="Imported export",
            bot_token_encrypted="",  # nothing to call back to; import only
            installed_by=current_user.id,
        )
    install.last_synced_at = datetime.utcnow()
    session.add(install)
    await session.commit()

    return {"indexed": counts, "messages": sum(counts.values())}
