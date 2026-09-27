"""The Chrome extension: whisper inside Google Meet, with no bot in the call.

    POST   /api/extension/pair-codes        (signed in)  make a one-time code
    POST   /api/extension/pair              (no auth)    swap the code for a token
    GET    /api/extension/devices           (signed in)  paired browsers
    DELETE /api/extension/devices/{id}      (signed in)  disconnect one
    GET    /api/extension/me                (extension)  who am I paired as
    POST   /api/extension/meet-session      (extension)  the whisper session for a Meet

Pairing is a code rather than a login inside the extension: GitHub-only
accounts have no password to type, and the extension must never hold a full
login token. What it gets is scoped (core/auth.py): whisper routes only, one
workspace, revocable per browser.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.core.auth import create_extension_token, get_current_user
from app.core.workspace import get_current_workspace
from app.database import get_session
from app.models import (
    ExtensionDevice,
    ExtensionPairing,
    User,
    WhisperSession,
    WhisperSource,
    WhisperStatus,
    Workspace,
)

router = APIRouter()

# Read aloud and typed by hand: no 0/O, 1/I/L.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_TTL = timedelta(minutes=5)
_MEET_CODE = re.compile(r"meet\.google\.com/([a-z]{3}-[a-z]{4}-[a-z]{3})", re.I)


def new_code() -> str:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def code_hash(code: str) -> str:
    """Normalised before hashing, so "abcd efgh" and "ABCD-EFGH" match."""
    clean = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
    return hashlib.sha256(clean.encode()).hexdigest()


def meet_code(url: str) -> Optional[str]:
    m = _MEET_CODE.search(url or "")
    return m.group(1).lower() if m else None


class PairBody(SQLModel):
    code: str
    device_name: str = "Chrome"


class MeetBody(SQLModel):
    meet_url: str
    title: Optional[str] = None


# ── pairing ──────────────────────────────────────────────────────────────

@router.post("/pair-codes", status_code=201)
async def make_code(workspace: Workspace = Depends(get_current_workspace),
                    session: AsyncSession = Depends(get_session),
                    user: User = Depends(get_current_user)):
    code = new_code()
    session.add(ExtensionPairing(code_hash=code_hash(code), user_id=user.id, workspace_id=workspace.id,
                                 expires_at=datetime.utcnow() + CODE_TTL))
    await session.commit()
    # The API address travels with the code so the extension needs nothing
    # else typed into it.
    return {"code": code, "expires_in": int(CODE_TTL.total_seconds()),
            "api_base": get_settings().public_base_url, "workspace": workspace.name}


@router.post("/pair")
async def pair(body: PairBody, session: AsyncSession = Depends(get_session)):
    """Unauthenticated by design — the code is the credential. Single use,
    five minutes, and one generic 400 for every failure so a guess learns
    nothing about which codes exist."""
    pairing = (await session.execute(select(ExtensionPairing).where(
        ExtensionPairing.code_hash == code_hash(body.code)))).scalars().first()
    if (pairing is None or pairing.used_at is not None
            or pairing.expires_at < datetime.utcnow()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That code is wrong or expired — make a new one in Photon")
    user = await session.get(User, pairing.user_id)
    workspace = await session.get(Workspace, pairing.workspace_id)
    if user is None or workspace is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That code is wrong or expired — make a new one in Photon")
    pairing.used_at = datetime.utcnow()
    device = ExtensionDevice(user_id=user.id, workspace_id=workspace.id,
                             name=(body.device_name or "Chrome").strip()[:60] or "Chrome")
    session.add(pairing)
    session.add(device)
    await session.commit()
    await session.refresh(device)
    token, expires = create_extension_token(user.id, user.email, workspace.id, device.id)
    return {"token": token, "expires_at": expires, "workspace_id": workspace.id,
            "workspace_name": workspace.name, "email": user.email, "device_id": device.id}


@router.get("/devices")
async def devices(session: AsyncSession = Depends(get_session),
                  user: User = Depends(get_current_user)):
    rows = (await session.execute(select(ExtensionDevice).where(
        ExtensionDevice.user_id == user.id, ExtensionDevice.revoked_at.is_(None),
    ).order_by(ExtensionDevice.created_at.desc()))).scalars().all()
    return [{"id": d.id, "name": d.name, "created_at": d.created_at,
             "last_seen_at": d.last_seen_at} for d in rows]


@router.delete("/devices/{device_id}", status_code=204)
async def revoke(device_id: str, session: AsyncSession = Depends(get_session),
                 user: User = Depends(get_current_user)):
    device = await session.get(ExtensionDevice, device_id)
    if device is None or device.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such device")
    device.revoked_at = datetime.utcnow()
    session.add(device)
    await session.commit()


# ── used by the extension ────────────────────────────────────────────────

@router.get("/me")
async def me(workspace: Workspace = Depends(get_current_workspace),
             user: User = Depends(get_current_user)):
    return {"email": user.email, "workspace_id": workspace.id, "workspace_name": workspace.name}


@router.post("/meet-session")
async def meet_session(body: MeetBody, workspace: Workspace = Depends(get_current_workspace),
                       session: AsyncSession = Depends(get_session),
                       user: User = Depends(get_current_user)):
    """The whisper session for this Meet, reused across reloads.

    Keyed on the Meet code and the member: a page refresh or a second tab on
    the same call must land in the same session, not start a fresh one that
    loses what was already heard.
    """
    code = meet_code(body.meet_url)
    if not code:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Not a Google Meet link")
    ref = f"meet:{code}"
    since = datetime.utcnow() - timedelta(hours=12)
    existing = (await session.execute(select(WhisperSession).where(
        WhisperSession.workspace_id == workspace.id, WhisperSession.created_by == user.id,
        WhisperSession.external_ref == ref, WhisperSession.status == WhisperStatus.LIVE,
        WhisperSession.created_at >= since,
    ))).scalars().first()
    if existing:
        return {"session_id": existing.id, "reused": True}
    whisper = WhisperSession(workspace_id=workspace.id, created_by=user.id,
                             title=(body.title or "").strip()[:120] or f"Google Meet {code}",
                             source=WhisperSource.EXTERNAL, external_ref=ref)
    session.add(whisper)
    await session.commit()
    await session.refresh(whisper)
    return {"session_id": whisper.id, "reused": False}
