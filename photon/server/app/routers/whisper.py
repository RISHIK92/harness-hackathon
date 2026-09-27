"""Whisper mode — Photon listening to someone else's meeting, answering only
to your side of it.

Speak mode puts Photon in the room. Whisper mode keeps it out: the client
hears nothing, sees no bot in the participant list where the transcript
comes from a capture they cannot see, and every suggestion lands in a
private thread belonging to ONE workspace member.

Privacy here is a row, not a UI choice. A thread has a `user_id`, and every
read re-checks it against the caller's session (`_thread_for_caller`). The
client is not a workspace member, so there is nothing for them to be shown
even if they found the endpoint.
"""
from __future__ import annotations

import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import SQLModel, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_current_user
from app.core.workspace import get_current_workspace
from app.database import get_session
from app.models import (
    Meeting,
    User,
    WhisperLine,
    WhisperLineCreate,
    WhisperMessage,
    WhisperMessageRead,
    WhisperRole,
    WhisperSession,
    WhisperSessionRead,
    WhisperSource,
    WhisperStatus,
    WhisperThread,
    Workspace,
)
from app.config import get_settings
from app.services.whisper.engine import answer_in_thread, schedule_suggestion, should_suggest
from app.services.whisper.participants import resolve_is_client
from app.services.whisper.providers import (
    BotProviderUnavailable,
    bot_status,
    is_configured,
    lines_from_webhook,
    send_bot,
    stop_bot,
)

router = APIRouter()


class WhisperSessionCreate(SQLModel):
    title: Optional[str] = None
    source: WhisperSource = WhisperSource.EXTERNAL
    external_ref: Optional[str] = None
    meeting_slug: Optional[str] = None


class AskRequest(SQLModel):
    question: str


async def _session_for_workspace(session: AsyncSession, session_id: str, workspace: Workspace) -> WhisperSession:
    whisper = await session.get(WhisperSession, session_id)
    # 404 rather than 403 for another tenant's session, same as everywhere
    # else: a 403 confirms the id exists.
    if not whisper or whisper.workspace_id != workspace.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such whisper session")
    return whisper


async def _thread_for_caller(session: AsyncSession, thread_id: str, user: User) -> WhisperThread:
    thread = await session.get(WhisperThread, thread_id)
    if not thread or thread.user_id != user.id:
        # The single check that makes a thread private. Not "is a member of
        # the workspace" — a colleague must not read your thread either.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such thread")
    return thread


@router.post("/sessions", response_model=WhisperSessionRead, status_code=201)
async def create_session(
    payload: WhisperSessionCreate,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
    user: User = Depends(get_current_user),
):
    meeting_id = None
    if payload.meeting_slug:
        from app.services.meeting_slug import normalise

        meeting = (await session.execute(
            select(Meeting).where(Meeting.slug == normalise(payload.meeting_slug))
        )).scalars().first()
        if not meeting or meeting.workspace_id != workspace.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such meeting in this workspace")
        meeting_id = meeting.id
        # One live session per call. A whisper-mode call already has one
        # (created with the meeting), and a second member opening the panel
        # must join it, not fork a parallel session that the transcript
        # feed would never reach.
        existing = (await session.execute(select(WhisperSession).where(
            WhisperSession.meeting_id == meeting.id, WhisperSession.status == WhisperStatus.LIVE,
        ))).scalars().first()
        if existing:
            return WhisperSessionRead(**existing.model_dump(), line_count=0)

    whisper = WhisperSession(
        workspace_id=workspace.id,
        title=payload.title,
        source=payload.source,
        external_ref=payload.external_ref,
        meeting_id=meeting_id,
        created_by=user.id,
    )
    session.add(whisper)
    await session.commit()
    await session.refresh(whisper)
    return WhisperSessionRead(**whisper.model_dump(), line_count=0)


@router.get("/sessions", response_model=list[WhisperSessionRead])
async def list_sessions(
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
):
    rows = (await session.execute(
        select(WhisperSession)
        .where(WhisperSession.workspace_id == workspace.id)
        .order_by(WhisperSession.created_at.desc())
        .limit(50)
    )).scalars().all()
    counts = dict((await session.execute(
        select(WhisperLine.session_id, func.count(WhisperLine.id)).group_by(WhisperLine.session_id)
    )).all())
    return [WhisperSessionRead(**w.model_dump(), line_count=counts.get(w.id, 0)) for w in rows]


@router.post("/sessions/{session_id}/lines", status_code=201)
async def ingest_line(
    session_id: str,
    payload: WhisperLineCreate,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
):
    """One line of the meeting, from whatever is capturing it.

    Source-agnostic on purpose — a bot vendor's webhook, a browser
    extension, a desktop capture and Photon's own worker all post the same
    shape. That is what stops whisper being coupled to one vendor.
    """
    whisper = await _session_for_workspace(session, session_id, workspace)
    if whisper.status is WhisperStatus.ENDED:
        raise HTTPException(status.HTTP_409_CONFLICT, "this session has ended")

    return await _ingest(session, whisper, payload)


async def _ingest(session: AsyncSession, whisper: WhisperSession, payload: WhisperLineCreate) -> dict:
    """One line in, one suggestion decision. Shared by the authenticated
    endpoint and the vendor webhook so the two can never drift — the whole
    point of source-agnostic ingest is that every path behaves identically
    once the line exists."""
    is_client = payload.is_client
    if is_client is None:
        is_client = await resolve_is_client(session, whisper.workspace_id, payload.speaker_name,
                                            payload.speaker_email)

    line = WhisperLine(
        session_id=whisper.id,
        speaker_name=payload.speaker_name,
        text=payload.text,
        is_client=is_client,
    )
    session.add(line)
    await session.commit()
    await session.refresh(line)

    queued = bool(line.is_client and should_suggest(line.text))
    if queued:
        # Answered in the background — see schedule_suggestion. The thread
        # gets the suggestion a couple of seconds later; the caller (a vendor
        # webhook above all) gets its acknowledgement now.
        schedule_suggestion(whisper.id, line.id)
    # suggested_to_threads is kept for callers that read it; it is always 0
    # now that suggesting happens after the response.
    return {"id": line.id, "is_client": line.is_client, "suggested_to_threads": 0,
            "suggestion_queued": queued}


@router.get("/sessions/{session_id}/lines")
async def list_lines(
    session_id: str,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
):
    whisper = await _session_for_workspace(session, session_id, workspace)
    rows = (await session.execute(
        select(WhisperLine).where(WhisperLine.session_id == whisper.id).order_by(WhisperLine.created_at)
    )).scalars().all()
    return [
        {"id": r.id, "speaker_name": r.speaker_name, "text": r.text,
         "is_client": r.is_client, "created_at": r.created_at}
        for r in rows
    ]


@router.post("/sessions/{session_id}/end", response_model=WhisperSessionRead)
async def end_session(
    session_id: str,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
):
    """Ends the LIVE part only. Threads and their messages stay — the whole
    point of a persisted thread is that it outlives the call it came from."""
    whisper = await _session_for_workspace(session, session_id, workspace)
    whisper.status = WhisperStatus.ENDED
    whisper.ended_at = datetime.utcnow()
    session.add(whisper)
    await session.commit()
    await session.refresh(whisper)
    return WhisperSessionRead(**whisper.model_dump(), line_count=0)


@router.get("/sessions/{session_id}/thread")
async def my_thread(
    session_id: str,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
    user: User = Depends(get_current_user),
):
    """The caller's own thread, created on demand. There is no endpoint that
    returns anyone else's."""
    whisper = await _session_for_workspace(session, session_id, workspace)
    thread = (await session.execute(
        select(WhisperThread).where(
            WhisperThread.session_id == whisper.id, WhisperThread.user_id == user.id
        )
    )).scalars().first()
    if thread is None:
        thread = WhisperThread(session_id=whisper.id, workspace_id=workspace.id, user_id=user.id)
        session.add(thread)
        await session.commit()
        await session.refresh(thread)
    return {"id": thread.id, "session_id": whisper.id, "created_at": thread.created_at}


@router.get("/threads/{thread_id}/messages", response_model=list[WhisperMessageRead])
async def thread_messages(
    thread_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    thread = await _thread_for_caller(session, thread_id, user)
    rows = (await session.execute(
        select(WhisperMessage)
        .where(WhisperMessage.thread_id == thread.id)
        .order_by(WhisperMessage.created_at)
    )).scalars().all()
    return [WhisperMessageRead(**m.model_dump()) for m in rows]


@router.post("/threads/{thread_id}/ask", response_model=WhisperMessageRead)
async def ask_in_thread(
    thread_id: str,
    payload: AskRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    thread = await _thread_for_caller(session, thread_id, user)
    whisper = await session.get(WhisperSession, thread.session_id)
    if not whisper:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such whisper session")
    message = await answer_in_thread(session, whisper, thread, payload.question)
    return WhisperMessageRead(**message.model_dump())


# ── Meeting-bot vendor: dispatch, and the transcript coming back ─────────


def _webhook_url(whisper: WhisperSession) -> str:
    return (
        f"{get_settings().public_base_url}/api/whisper/webhooks/"
        f"{whisper.id}/{whisper.webhook_secret}"
    )


@router.get("/sessions/{session_id}/webhook")
async def webhook_url(
    session_id: str,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
):
    """Where a capture source should POST this session's transcript.

    Its own endpoint rather than a field on the session listing: the URL
    carries the secret, and a credential that rides along with every list
    response ends up in logs and screenshots.
    """
    whisper = await _session_for_workspace(session, session_id, workspace)
    return {
        "url": _webhook_url(whisper),
        "note": "POST vendor transcript events here, or {speaker_name, text} "
                "directly. is_client is resolved from the speaker name when omitted.",
        "bot_provider_configured": is_configured(),
    }


class BotRequest(SQLModel):
    meeting_url: str
    bot_name: Optional[str] = None


class JoinRequest(SQLModel):
    meeting_url: str
    title: Optional[str] = None


# What the rep is told for each vendor state. The one that matters is the
# waiting room: nothing Photon can do gets the bot in — a person in the
# meeting has to click Admit, so the UI must SAY that, not show a spinner.
BOT_STATES = {
    "joining_call": ("Joining the meeting…", None),
    "in_waiting_room": ("Waiting to be let in", "Admit “{name}” from the meeting's waiting room."),
    "in_call_not_recording": ("In the meeting, starting to listen…", None),
    "recording_permission_allowed": ("Listening", None),
    "in_call_recording": ("Listening", None),
    "recording_permission_denied": ("Not allowed to record", "The host declined recording — whisper can't hear this meeting."),
    "call_ended": ("Meeting ended", None),
    "done": ("Meeting ended", None),
    "fatal": ("Couldn't join", "Check the link is a live Meet, Zoom or Teams meeting and try again."),
}

_MEETING_HOSTS = ("meet.google.com", "zoom.us", "teams.microsoft.com", "teams.live.com")


def _check_meeting_url(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(host == h or host.endswith("." + h) for h in _MEETING_HOSTS):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Paste a Google Meet, Zoom or Microsoft Teams meeting link")
    return url.strip()


def _check_reachable() -> None:
    """The vendor posts the transcript to public_base_url. localhost is not
    reachable from Recall's servers, and the failure mode — a bot that joins,
    listens, and delivers nothing — is silent, so refuse up front instead."""
    base = get_settings().public_base_url
    if any(h in base for h in ("localhost", "127.0.0.1", "0.0.0.0")):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The meeting bot can't reach {base}. Start the stack with --with-ngrok "
                            "and set PUBLIC_BASE_URL to the tunnel URL.")


async def _bot_identity(session: AsyncSession, workspace: Workspace, user: User) -> tuple[str, str]:
    """The name in the participant list, and the line posted to the chat."""
    from app.models import AgentProfile
    from app.services.escalation import first_name

    profile = (await session.execute(select(AgentProfile).where(
        AgentProfile.workspace_id == workspace.id, AgentProfile.user_id == user.id))).scalars().first()
    who = first_name(profile.display_name if profile else None, user.email, user.github_login)
    agent = workspace.agent_name or "Photon"
    name = f"{who}'s {agent} (notes)"
    return name, (f"Hi — I'm {who}'s notetaker from {agent}. I'm transcribing this call for "
                  f"{who} only and won't speak. Ask {who} if you'd like me to leave.")


def _bot_view(whisper: WhisperSession, bot_name: str | None = None) -> dict:
    label, action = BOT_STATES.get(whisper.bot_status or "", ("Starting…", None))
    return {
        "session_id": whisper.id,
        "bot_id": whisper.external_ref,
        "status": whisper.bot_status,
        "label": label,
        "action": action.format(name=bot_name or "the notetaker") if action else None,
        "visible_to_participants": True,
    }


async def _dispatch(session: AsyncSession, whisper: WhisperSession, workspace: Workspace,
                    user: User, meeting_url: str, bot_name: str | None) -> dict:
    _check_reachable()
    name, announce = await _bot_identity(session, workspace, user)
    try:
        bot_id = send_bot(_check_meeting_url(meeting_url), _webhook_url(whisper), bot_name or name,
                          announce=announce, metadata={"whisper_session_id": whisper.id})
    except BotProviderUnavailable as exc:
        # 501, not 500: nothing is broken, the capability is simply not
        # configured, and the message says exactly what to do instead.
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"the meeting-bot vendor refused: {exc}")
    whisper.source = WhisperSource.BOT
    whisper.external_ref = bot_id
    whisper.bot_status = "joining_call"
    whisper.bot_status_at = datetime.utcnow()
    session.add(whisper)
    await session.commit()
    return _bot_view(whisper, bot_name or name)


@router.post("/join", status_code=201)
async def join_meeting(
    payload: JoinRequest,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
    user: User = Depends(get_current_user),
):
    """Paste a meeting link, get a whisper. One step: the session and the
    bot are created together, so there is no half-state where a session
    exists and nothing is listening to it."""
    _check_reachable()
    _check_meeting_url(payload.meeting_url)
    whisper = WhisperSession(workspace_id=workspace.id, title=payload.title or "Meeting",
                             source=WhisperSource.BOT, external_ref=None,
                             created_by=user.id)
    session.add(whisper)
    await session.commit()
    await session.refresh(whisper)
    try:
        view = await _dispatch(session, whisper, workspace, user, payload.meeting_url, None)
    except HTTPException:
        # No bot, no session: a session nobody is listening to would look
        # exactly like a quiet meeting.
        await session.delete(whisper)
        await session.commit()
        raise
    return view


@router.post("/sessions/{session_id}/bot")
async def dispatch_bot(
    session_id: str,
    payload: BotRequest,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
    user: User = Depends(get_current_user),
):
    """Send a meeting bot into an existing session's Meet / Teams / Zoom call.
    The bot IS visible in the participant list and announces itself in the
    meeting chat — nothing about a vendor bot is silent."""
    whisper = await _session_for_workspace(session, session_id, workspace)
    return await _dispatch(session, whisper, workspace, user, payload.meeting_url, payload.bot_name)


@router.get("/sessions/{session_id}/bot")
async def bot_state(
    session_id: str,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
):
    """Where the bot is right now, in words the rep can act on."""
    whisper = await _session_for_workspace(session, session_id, workspace)
    if not whisper.external_ref:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no bot for this session")
    try:
        code = bot_status(whisper.external_ref)
    except BotProviderUnavailable as exc:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc))
    except Exception:  # noqa: BLE001 — a vendor blip keeps the last known state
        code = whisper.bot_status
    if code and code != whisper.bot_status:
        whisper.bot_status, whisper.bot_status_at = code, datetime.utcnow()
        session.add(whisper)
        await session.commit()
    return _bot_view(whisper)


@router.post("/sessions/{session_id}/bot/stop")
async def stop_meeting_bot(
    session_id: str,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
):
    whisper = await _session_for_workspace(session, session_id, workspace)
    if not whisper.external_ref:
        raise HTTPException(status.HTTP_409_CONFLICT, "no bot is running for this session")
    try:
        stop_bot(whisper.external_ref)
    except BotProviderUnavailable as exc:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc))
    return {"stopped": whisper.external_ref}


@router.post("/webhooks/{session_id}/{secret}", status_code=202)
async def transcript_webhook(
    session_id: str,
    secret: str,
    payload: dict,
    session: AsyncSession = Depends(get_session),
):
    """Transcript arriving from outside — a bot vendor, an extension, a capture.

    Unauthenticated by necessity: none of those carry a user session. The
    per-session secret in the path is the credential, compared in constant
    time. It is a bearer token in a URL, with the usual consequence that it
    can end up in an access log, so it is scoped to ONE session and dies
    when that session ends rather than being a standing key.

    Accepts a vendor transcript event OR a plain {speaker_name, text} body,
    because the same URL is the one you hand a hand-rolled capture script.
    """
    whisper = await session.get(WhisperSession, session_id)
    if not whisper or not secrets.compare_digest(secret, whisper.webhook_secret or ""):
        # One 404 for both "no such session" and "wrong secret": distinct
        # answers would turn this into an oracle for valid session ids.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such whisper webhook")
    if whisper.status is WhisperStatus.ENDED:
        raise HTTPException(status.HTTP_409_CONFLICT, "this session has ended")

    if "speaker_name" in payload and "text" in payload:
        incoming = [{"speaker_name": payload["speaker_name"], "text": payload["text"],
                     "is_client": payload.get("is_client")}]
    else:
        incoming = lines_from_webhook(payload)

    results = []
    for item in incoming:
        results.append(await _ingest(session, whisper, WhisperLineCreate(**item)))
    return {"accepted": len(results), "results": results}
