"""Meetings and their shared transcript.

A meeting is identified by its slug (abcd-efgh), which is also the LiveKit
room name — one identifier for the link, the room and the transcript.

Transcript writes come from the call-agent worker, which is the only thing
that sees every finalized turn. Reads are workspace-scoped like everything
else.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_current_user
from app.core.workspace import get_current_workspace, membership_for, require_role
from app.database import get_session
from app.models import (
    KnockRead,
    KnockStatus,
    Meeting,
    MeetingKnock,
    MeetingRead,
    TranscriptEntry,
    TranscriptEntryCreate,
    TranscriptRole,
    User,
    Workspace,
    WorkspaceRole,
)
from app.services.meeting_slug import new_slug, normalise

router = APIRouter()


class MeetingCreate(SQLModel):
    title: Optional[str] = None
    bot_types: list[str] = ["support"]
    language_mode: str = "english"
    # None = fall back to the workspace defaults (GitHub + custom docs where
    # they have data). An empty list is a deliberate "no sources".
    enabled_sources: Optional[list[str]] = None
    # "member" (the agent attends as YOUR agent) or "company" (it represents
    # the workspace — only when an owner has enabled the org agent).
    attends_as: str = "member"
    # "speak" or "whisper" — see Meeting.mode.
    mode: str = "speak"


class MeetingConfig(SQLModel):
    bot_types: Optional[list[str]] = None
    language_mode: Optional[str] = None
    enabled_sources: Optional[list[str]] = None
    attends_as: Optional[str] = None


def _check_attends_as(value: str, workspace: Workspace) -> str:
    if value not in ("member", "company"):
        raise HTTPException(status_code=422, detail="attends_as must be 'member' or 'company'")
    if value == "company" and not workspace.org_agent_enabled:
        raise HTTPException(
            status_code=403,
            detail="Attending on behalf of the company needs the org agent, which a workspace owner turns on",
        )
    return value


@router.post("", response_model=MeetingRead, status_code=201)
async def create_meeting(
    payload: MeetingCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    # A viewer may start a call: joining and asking questions is exactly
    # what the viewer role is for.
    workspace: Workspace = Depends(require_role(WorkspaceRole.VIEWER)),
):
    for _ in range(5):  # collisions are vanishingly unlikely; retry anyway
        slug = new_slug()
        if not (await session.execute(select(Meeting).where(Meeting.slug == slug))).scalars().first():
            break
    else:
        raise HTTPException(status_code=500, detail="Could not allocate a meeting code")

    from app.services.tool_availability import (
        default_enabled_keys,
        has_any_source,
        source_groups,
    )

    groups = await source_groups(session, workspace.id)
    if not has_any_source(groups):
        # Refused rather than allowed-and-useless: a call where the agent
        # can answer nothing looks broken to everyone on it, and the fix
        # (connect a source) is not discoverable from inside the call.
        raise HTTPException(
            status_code=409,
            detail=(
                "This workspace has no sources connected yet — index a repository or upload a "
                "document before starting a call, or the agent will have nothing to answer from."
            ),
        )

    meeting = Meeting(
        slug=slug,
        workspace_id=workspace.id,
        title=payload.title,
        created_by=current_user.id,
        attends_as=_check_attends_as(payload.attends_as or "member", workspace),
        mode=payload.mode if payload.mode in ("speak", "whisper") else "speak",
        # The person the agent is standing in for, and the one it escalates
        # to. For a company call, the person who set it up answers for it.
        represents_user_id=current_user.id,
        bot_types=payload.bot_types or ["support"],
        language_mode=payload.language_mode or "english",
        enabled_sources=(
            payload.enabled_sources
            if payload.enabled_sources is not None
            # A company call defaults to what the admin scoped the company
            # agent to — never wider than the workspace actually has.
            else [k for k in default_enabled_keys(groups) if k in workspace.org_agent_sources]
            if payload.attends_as == "company" and workspace.org_agent_sources is not None
            else default_enabled_keys(groups)
        ),
    )
    session.add(meeting)
    await session.commit()
    await session.refresh(meeting)
    if meeting.mode == "whisper":
        # A whisper call IS a whisper session: created with the meeting so
        # the first client question is caught even if nobody opens the
        # panel, and so there is exactly one ingest path (the worker's
        # transcript) rather than a browser bridge racing it.
        from app.models import WhisperSession, WhisperSource

        session.add(WhisperSession(
            workspace_id=workspace.id, title=payload.title or f"Call {meeting.slug}",
            source=WhisperSource.PHOTON, meeting_id=meeting.id, created_by=current_user.id,
        ))
        await session.commit()
    return meeting


@router.get("", response_model=list[MeetingRead])
async def list_meetings(
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
):
    result = await session.execute(
        select(Meeting)
        .where(Meeting.workspace_id == workspace.id)
        .order_by(Meeting.created_at.desc())
        .limit(50)
    )
    return result.scalars().all()


async def _optional_user(session: AsyncSession, authorization: Optional[str]) -> Optional[User]:
    """Resolve a bearer token if one was sent, without requiring one.

    The knock endpoint has to serve both a signed-in colleague and an
    external client with no account, so authentication here is a fact to
    establish rather than a gate to pass.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    from jose import JWTError, jwt

    from app.config import get_settings

    settings = get_settings()
    try:
        payload = jwt.decode(
            authorization.split(" ", 1)[1],
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        return None
    if payload.get("scope"):
        # A scoped token (the Chrome extension's) is not a signed-in member:
        # it must never let someone skip a meeting's waiting room.
        return None
    user_id = payload.get("sub")
    return await session.get(User, user_id) if user_id else None


async def _meeting_by_slug(session: AsyncSession, slug: str) -> Meeting:
    result = await session.execute(select(Meeting).where(Meeting.slug == normalise(slug)))
    meeting = result.scalars().first()
    if not meeting:
        raise HTTPException(status_code=404, detail="No meeting with that code")
    return meeting


@router.get("/{slug}", response_model=MeetingRead)
async def get_meeting(
    slug: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Resolve a code to a meeting.

    Deliberately NOT workspace-scoped: someone joining a call from a shared
    link may not be a member of the workspace at all (an external client on
    a support call). Membership decides what the AGENT will tell them, not
    whether the room exists.
    """
    return await _meeting_by_slug(session, slug)


@router.post("/{slug}/transcript", status_code=201)
async def append_transcript(
    slug: str,
    payload: TranscriptEntryCreate,
    session: AsyncSession = Depends(get_session),
):
    """Append one line. Called by the call-agent worker as turns finalize.

    Unauthenticated for the same reason /api/agent/ask is (demo scope, see
    CLAUDE.md): the worker is a server-side component with no user session.
    Before this is exposed beyond localhost it needs a shared secret — noted
    rather than pretended away.
    """
    meeting = await _meeting_by_slug(session, slug)

    speaker_user_id = None
    identity = payload.speaker_identity or ""
    if identity.startswith("user:"):
        # Identity is signed into the LiveKit token by our own API, so the
        # user id in it is trustworthy — a guest cannot type their way into
        # being someone else (see client/app/api/livekit-token/route.ts).
        candidate = identity.split("user:", 1)[1]
        if await session.get(User, candidate):
            speaker_user_id = candidate

    entry = TranscriptEntry(
        meeting_id=meeting.id,
        role=payload.role,
        speaker_name=payload.speaker_name,
        speaker_identity=payload.speaker_identity,
        speaker_user_id=speaker_user_id,
        text=payload.text,
    )
    session.add(entry)
    await session.commit()

    # Feed a whisper session bound to this meeting, if one is live.
    #
    # This is the path that needs no vendor at all: Photon's own call already
    # transcribes every turn, so whisper works on it today, and the meeting-bot
    # adapters (app/services/whisper/providers.py) post to the same place for
    # meetings on platforms we don't own.
    #
    # A human line is a "client" line for whisper's purposes ONLY when the
    # speaker is not a signed-in workspace member — that is the same
    # distinction whisper cares about (answer what the customer asks, not what
    # your colleague says), and speaker_user_id above is the trustworthy way
    # to tell, since it comes from a signed token.
    await _feed_whisper(session, meeting, payload, speaker_user_id)
    await _capture_commitment(session, meeting, payload, speaker_user_id)
    return {"ok": True}


async def _capture_commitment(session, meeting, payload, speaker_user_id) -> None:
    """A fix promised on our side of the call becomes a DRAFT agent job for
    the person who promised it (or the member the agent attends for, when
    the agent itself said it). Drafts are never planned until confirmed.

    Best-effort, like whisper: it must never cost the transcript write."""
    try:
        from app.models import AgentJob, AgentJobSource, AgentJobStatus
        from app.services import commitments

        ours = payload.role is TranscriptRole.AGENT or speaker_user_id is not None
        if not ours:
            return
        found = commitments.detect(payload.text)
        if not found:
            return
        owner = speaker_user_id or meeting.represents_user_id or meeting.created_by
        if not owner:
            return
        existing = (await session.execute(select(AgentJob).where(
            AgentJob.meeting_id == meeting.id,
            AgentJob.status == AgentJobStatus.DRAFT.value,
        ))).scalars().all()
        if any(found.sentence in (j.issue_text or "") for j in existing):
            return
        recent = (await session.execute(
            select(TranscriptEntry).where(
                TranscriptEntry.meeting_id == meeting.id,
                TranscriptEntry.role == TranscriptRole.HUMAN,
                TranscriptEntry.speaker_user_id.is_(None),
            ).order_by(TranscriptEntry.created_at.desc()).limit(4)
        )).scalars().all()
        problem = [e.text for e in reversed(recent)]
        speaker = "The agent" if payload.role is TranscriptRole.AGENT else payload.speaker_name
        session.add(AgentJob(
            workspace_id=meeting.workspace_id, owner_user_id=owner, repo_id=None,
            meeting_id=meeting.id, status=AgentJobStatus.DRAFT.value,
            source=AgentJobSource.MEETING.value,
            title=commitments.title_for(found, problem),
            issue_text=commitments.issue_text_for(found, problem, meeting.title, speaker),
        ))
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        import structlog
        structlog.get_logger().warning("meetings.commitment_capture_failed", error=str(exc)[:200])


async def _feed_whisper(session, meeting, payload, speaker_user_id) -> None:
    """Best-effort: whisper is an assist, and a failure here must never cost
    the transcript write that already succeeded."""
    if payload.role is not TranscriptRole.HUMAN:
        return
    try:
        from app.models import WhisperLine, WhisperSession, WhisperStatus
        from app.services.whisper.engine import schedule_suggestion, should_suggest

        whisper = (await session.execute(
            select(WhisperSession).where(
                WhisperSession.meeting_id == meeting.id,
                WhisperSession.status == WhisperStatus.LIVE,
            )
        )).scalars().first()
        if whisper is None:
            return
        line = WhisperLine(
            session_id=whisper.id,
            speaker_name=payload.speaker_name,
            text=payload.text,
            is_client=speaker_user_id is None,
        )
        session.add(line)
        await session.commit()
        await session.refresh(line)
        if line.is_client and should_suggest(line.text):
            # Off the request: this endpoint is on the worker's transcript
            # path, and a 2-3s agent turn here would stall every line after it.
            schedule_suggestion(whisper.id, line.id)
    except Exception as exc:  # noqa: BLE001
        import structlog

        structlog.get_logger().warning("meetings.whisper_feed_failed", error=str(exc))


@router.get("/{slug}/transcript.md", response_class=PlainTextResponse)
async def transcript_markdown(
    slug: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    download: bool = Query(default=False),
):
    """The whole call as one markdown document.

    Rendered from rows rather than stored as an appended blob: several
    people and the agent all speak during a call, and concurrent
    read-modify-write on a single text column loses lines silently.
    """
    meeting = await _meeting_by_slug(session, slug)
    if not await membership_for(session, meeting.workspace_id, current_user.id):
        # The room is discoverable by code; what was SAID in it is not.
        raise HTTPException(status_code=404, detail="No meeting with that code")

    result = await session.execute(
        select(TranscriptEntry)
        .where(TranscriptEntry.meeting_id == meeting.id)
        .order_by(TranscriptEntry.created_at)
    )
    entries = result.scalars().all()

    lines = [
        f"# {meeting.title or 'Meeting'} — {meeting.slug}",
        "",
        f"*{meeting.created_at:%Y-%m-%d %H:%M} UTC · {len(entries)} entries*",
        "",
    ]
    for e in entries:
        who = "**Photon**" if e.role == TranscriptRole.AGENT else f"**{e.speaker_name}**"
        lines.append(f"`{e.created_at:%H:%M:%S}` {who}: {e.text}")
        lines.append("")

    body = "\n".join(lines)
    headers = (
        {"Content-Disposition": f'attachment; filename="{meeting.slug}.md"'} if download else {}
    )
    return PlainTextResponse(body, media_type="text/markdown; charset=utf-8", headers=headers)


@router.post("/{slug}/end", response_model=MeetingRead)
async def end_meeting(
    slug: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    meeting = await _meeting_by_slug(session, slug)
    if not await membership_for(session, meeting.workspace_id, current_user.id):
        raise HTTPException(status_code=404, detail="No meeting with that code")
    meeting.ended_at = meeting.ended_at or datetime.utcnow()
    session.add(meeting)
    await session.commit()
    await session.refresh(meeting)
    return meeting


@router.get("/options/catalog")
async def call_options(
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
):
    """Everything the pre-call screen needs: bot types, language modes, and
    which sources this workspace can actually use right now."""
    from app.agent.personas import catalog as persona_catalog
    from app.services.tool_availability import default_enabled_keys, source_groups

    groups = await source_groups(session, workspace.id)
    return {
        "bot_types": persona_catalog(),
        # Described by what the caller gets, not by which vendor provides
        # it. Whoever picks this is choosing how the call should sound and
        # which languages it must handle; the supplier behind that is an
        # implementation detail that can change without the choice changing.
        "language_modes": [
            {"key": "english", "label": "English only",
             "detail": "Fastest replies. English voices."},
            {"key": "multilingual", "label": "Multilingual",
             "detail": "Telugu, Tamil, Hindi and English. Slightly slower to speak."},
        ],
        "sources": [
            {
                "key": g.key, "label": g.label, "available": g.available,
                "detail": g.detail, "default_enabled": g.default_enabled,
                "coming_soon": g.coming_soon, "tools": g.tools, "is_mock": g.is_mock,
            }
            for g in groups
        ],
        "default_enabled": default_enabled_keys(groups),
    }


@router.patch("/{slug}/config", response_model=MeetingRead)
async def update_config(
    slug: str,
    payload: MeetingConfig,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Change a call's setup. Allowed mid-call on purpose: people discover
    they need Jira three questions in, and making them restart the call to
    enable it would be worse than letting them toggle it live."""
    meeting = await _meeting_by_slug(session, slug)
    if not await membership_for(session, meeting.workspace_id, current_user.id):
        raise HTTPException(status_code=404, detail="No meeting with that code")

    if payload.bot_types is not None:
        meeting.bot_types = payload.bot_types
    if payload.language_mode is not None:
        meeting.language_mode = payload.language_mode
    if payload.enabled_sources is not None:
        meeting.enabled_sources = payload.enabled_sources
    if payload.attends_as is not None:
        workspace = await session.get(Workspace, meeting.workspace_id)
        meeting.attends_as = _check_attends_as(payload.attends_as, workspace)
    session.add(meeting)
    await session.commit()
    await session.refresh(meeting)
    return meeting


@router.get("/{slug}/call-config")
async def call_config(slug: str, session: AsyncSession = Depends(get_session)):
    """Configuration the call-agent worker needs at job start.

    Unauthenticated, like /api/agent/ask, for the same reason: the worker is
    a server-side component with no user session (see CLAUDE.md). It returns
    no secrets and no content — only which voice stack and persona this room
    was configured with — but it does confirm a room exists, so it needs the
    same shared secret as the transcript endpoint before this is exposed
    beyond localhost.
    """
    meeting = await _meeting_by_slug(session, slug)
    workspace = await session.get(Workspace, meeting.workspace_id)
    from app.services.escalation import first_name
    from app.models import AgentProfile

    base_name = (workspace.agent_name if workspace else None) or "Photon"
    poc_id = meeting.represents_user_id or meeting.created_by
    poc = await session.get(User, poc_id) if poc_id else None
    profile = None
    if poc:
        profile = (await session.execute(select(AgentProfile).where(
            AgentProfile.workspace_id == meeting.workspace_id,
            AgentProfile.user_id == poc.id))).scalars().first()
    poc_name = first_name(profile.display_name if profile else None,
                          poc.email if poc else None, poc.github_login if poc else None) if poc else None
    company = meeting.attends_as == "company"
    return {
        "slug": meeting.slug,
        "workspace_id": meeting.workspace_id,
        # What the agent announces itself as on joining. A personal
        # workspace's auto-generated name ("rishik's workspace") is not a
        # company, so it stays unset and the agent introduces itself
        # without one rather than announcing something wrong.
        "org_name": None if (workspace and workspace.is_personal) else (workspace.name if workspace else None),
        # The worker announces itself with this and listens for it as the
        # wake word — people address the agent by name out loud, so the two
        # have to be the same string.
        "agent_name": base_name,
        # How it introduces itself: never as the person, always as their
        # agent — "Priya's Photon" — or as the company's.
        "attends_as": meeting.attends_as,
        # The worker builds a different listener for each: one linked
        # participant for speak, every participant's mic for whisper.
        "mode": meeting.mode,
        "display_name": base_name if company or not poc_name else f"{poc_name}'s {base_name}",
        "poc_name": poc_name,
        "bot_types": meeting.bot_types or ["support"],
        "language_mode": meeting.language_mode or "english",
        # The mapping lives here rather than in the worker so "multilingual
        # means Sarvam" is decided in one place.
        "voice_stack": "sarvam" if (meeting.language_mode or "english") == "multilingual" else "deepgram",
        "enabled_sources": meeting.enabled_sources,
    }


# ── Waiting room ─────────────────────────────────────────────────────────


class KnockBody(SQLModel):
    display_name: str = ""


@router.post("/{slug}/knock")
async def knock(
    slug: str,
    payload: KnockBody,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(default=None),
):
    """Ask to be let into a call.

    Deliberately open to unauthenticated callers — external clients join
    support calls by link and have no account here. What identifies them is
    the name they give, and the fact that a human inside the call has to
    approve it.

    A signed-in workspace MEMBER is admitted immediately: they already have
    access to everything in the call, so a queue would only teach people to
    click Admit without reading it.
    """
    meeting = await _meeting_by_slug(session, slug)

    user = await _optional_user(session, authorization)
    if user and await membership_for(session, meeting.workspace_id, user.id):
        record = MeetingKnock(
            meeting_id=meeting.id,
            display_name=payload.display_name.strip() or user.email,
            user_id=user.id,
            status=KnockStatus.ADMITTED,
            decided_at=datetime.utcnow(),
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return {"id": record.id, "status": record.status, "reason": "workspace member"}

    # A signed-in user always has a name we can show, even when they are
    # not a member of this workspace — asking them to type it again just to
    # queue is friction for nothing. Only true strangers must introduce
    # themselves.
    name = payload.display_name.strip() or (user.email if user else "")
    if not name:
        raise HTTPException(status_code=422, detail="Enter your name so someone can let you in")

    record = MeetingKnock(
        meeting_id=meeting.id, display_name=name, user_id=user.id if user else None
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return {"id": record.id, "status": record.status}


@router.get("/{slug}/knock/{knock_id}")
async def knock_status(
    slug: str, knock_id: str, session: AsyncSession = Depends(get_session)
):
    """Polled by whoever is waiting. Returns only their own status — it
    reveals nothing about the call or who else is in it."""
    meeting = await _meeting_by_slug(session, slug)
    record = await session.get(MeetingKnock, knock_id)
    if not record or record.meeting_id != meeting.id:
        raise HTTPException(status_code=404, detail="No such request")
    return {"id": record.id, "status": record.status}


@router.get("/{slug}/knocks", response_model=list[KnockRead])
async def pending_knocks(
    slug: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Who is waiting. Members only — the list is people's names."""
    meeting = await _meeting_by_slug(session, slug)
    if not await membership_for(session, meeting.workspace_id, current_user.id):
        raise HTTPException(status_code=404, detail="No meeting with that code")

    result = await session.execute(
        select(MeetingKnock)
        .where(MeetingKnock.meeting_id == meeting.id, MeetingKnock.status == KnockStatus.PENDING)
        .order_by(MeetingKnock.created_at)
    )
    return [
        KnockRead(
            id=k.id, display_name=k.display_name, status=k.status,
            created_at=k.created_at, is_member=k.user_id is not None,
        )
        for k in result.scalars().all()
    ]


class DecideKnockBody(SQLModel):
    admit: bool


@router.post("/{slug}/knocks/{knock_id}")
async def decide_knock(
    slug: str,
    knock_id: str,
    payload: DecideKnockBody,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Admit or deny. Any workspace member on the call can decide — waiting
    for one specific person while a customer sits outside is worse than
    trusting the colleagues already in the room."""
    meeting = await _meeting_by_slug(session, slug)
    if not await membership_for(session, meeting.workspace_id, current_user.id):
        raise HTTPException(status_code=404, detail="No meeting with that code")

    record = await session.get(MeetingKnock, knock_id)
    if not record or record.meeting_id != meeting.id:
        raise HTTPException(status_code=404, detail="No such request")
    if record.status != KnockStatus.PENDING:
        return {"id": record.id, "status": record.status}

    record.status = KnockStatus.ADMITTED if payload.admit else KnockStatus.DENIED
    record.decided_at = datetime.utcnow()
    record.decided_by = current_user.id
    session.add(record)
    await session.commit()
    return {"id": record.id, "status": record.status}


@router.get("/{slug}/admission/{knock_id}")
async def verify_admission(
    slug: str, knock_id: str, session: AsyncSession = Depends(get_session)
):
    """Checked by the token minter before it issues a join token.

    The waiting room is only real if the token cannot be obtained without
    passing through it.
    """
    meeting = await _meeting_by_slug(session, slug)
    record = await session.get(MeetingKnock, knock_id)
    admitted = bool(record and record.meeting_id == meeting.id and record.status == KnockStatus.ADMITTED)
    return {"admitted": admitted, "display_name": record.display_name if record else None}
