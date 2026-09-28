"""Escalations — an agent asking its person mid-meeting, with a clock.

    POST /api/escalations/assess          worker: should this turn escalate?
    GET  /api/escalations/{id}/status     worker: answered yet?
    GET  /api/escalations/mine            the member's inbox (console)
    POST /api/escalations/{id}/answer     the member replies
    POST /api/escalations/{id}/decline    the member says "skip it"

    GET  /api/escalations/profile         your agent profile
    PUT  /api/escalations/profile

The worker endpoints are unauthenticated for the same reason as
/api/meetings/{slug}/transcript (demo scope — the worker is a server-side
component with no user session); an escalation id is an unguessable uuid,
and nothing here can be listed without a user session.

Expiry is decided HERE, lazily, on the worker's poll — never by the worker's
own clock. One authority means the console can never accept an answer the
agent has already apologised for as if it were in time: a late reply is
kept, but as a follow-up, and says so.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import service_auth

from app.config import get_settings
from app.core.auth import get_current_user
from app.core.workspace import get_current_workspace
from app.database import AsyncSessionLocal, get_session
from app.models import (
    AgentProfile,
    Escalation,
    EscalationStatus,
    Meeting,
    User,
    Workspace,
)
from app.services import escalation as rules

log = structlog.get_logger()
router = APIRouter()


class AssessBody(SQLModel):
    meeting_slug: str
    question: str
    result: dict = {}
    history: list[dict] = []
    turn: int = 0


class AnswerBody(SQLModel):
    answer: str


class ProfileBody(SQLModel):
    display_name: Optional[str] = None
    always_escalate: list[str] = []
    may_commit_to: Optional[str] = None
    notes: Optional[str] = None


class EscalationRead(SQLModel):
    id: str
    status: str
    question: str
    topic: str
    importance: str
    reason: str
    context: list
    draft_answer: Optional[str]
    answer: Optional[str]
    meeting_title: Optional[str]
    notified_via: list
    created_at: datetime
    expires_at: datetime
    resolved_at: Optional[datetime]
    seconds_left: int = 0


def _read(e: Escalation) -> EscalationRead:
    left = int((e.expires_at - datetime.utcnow()).total_seconds()) if e.status == EscalationStatus.OPEN.value else 0
    return EscalationRead(**e.model_dump(), seconds_left=max(0, left))


async def _profile(session: AsyncSession, workspace_id: str, user_id: str) -> Optional[AgentProfile]:
    return (await session.execute(select(AgentProfile).where(
        AgentProfile.workspace_id == workspace_id, AgentProfile.user_id == user_id))).scalars().first()


async def _poc(session: AsyncSession, meeting: Meeting) -> tuple[Optional[User], str, bool]:
    poc_id = meeting.represents_user_id or meeting.created_by
    poc = await session.get(User, poc_id) if poc_id else None
    profile = await _profile(session, meeting.workspace_id, poc.id) if poc else None
    name = rules.first_name(profile.display_name if profile else None,
                            poc.email if poc else None, poc.github_login if poc else None)
    return poc, name, meeting.attends_as == "company"


# ── worker ───────────────────────────────────────────────────────────────

# Both worker routes need the worker's token for the meeting in question.
# They were open: anyone with a meeting code could put attacker-written
# questions in a member's inbox (and a Slack DM, once scopes allow it).

@router.post("/assess")
async def assess(body: AssessBody, background: BackgroundTasks,
                 session: AsyncSession = Depends(get_session),
                 worker_token: Optional[str] = Depends(service_auth.worker_token_header)):
    from app.services.meeting_slug import normalise

    meeting = (await session.execute(select(Meeting).where(Meeting.slug == normalise(body.meeting_slug)))).scalars().first()
    if not meeting:
        raise HTTPException(status_code=404, detail="No meeting with that code")
    service_auth.require_worker(worker_token, service_auth.meeting_scope(meeting.slug))
    poc, name, company = await _poc(session, meeting)
    if not poc:
        return {"escalate": False, "reason": "nobody to escalate to"}
    profile = await _profile(session, meeting.workspace_id, poc.id)
    verdict = rules.assess(body.question, body.result or {},
                           profile.always_escalate if profile else None)
    if not verdict.escalate:
        return {"escalate": False, "reason": verdict.reason}

    # One question open at a time per meeting: a second ping while the
    # member is still reading the first is how both get missed.
    open_one = (await session.execute(select(Escalation).where(
        Escalation.meeting_id == meeting.id,
        Escalation.status == EscalationStatus.OPEN.value,
        Escalation.expires_at > datetime.utcnow()))).scalars().first()
    if open_one:
        return {"escalate": False, "reason": "already waiting on an earlier question"}

    draft = (body.result or {}).get("answer") or None
    escalation = Escalation(
        workspace_id=meeting.workspace_id, meeting_id=meeting.id, meeting_title=meeting.title,
        poc_user_id=poc.id, question=body.question.strip(), topic=verdict.topic,
        importance=verdict.importance, reason=verdict.reason,
        context=[{"role": h.get("role"), "text": h.get("text")} for h in (body.history or [])][-6:],
        draft_answer=None if (body.result or {}).get("abstained") else draft,
        holding_line=rules.holding_line(name, verdict.topic, company, body.turn),
        expires_at=datetime.utcnow() + timedelta(seconds=rules.TIMEOUT_SECONDS),
        notified_via=["console"],
    )
    session.add(escalation)
    await session.commit()
    await session.refresh(escalation)
    background.add_task(_notify_slack, escalation.id)
    log.info("escalation.opened", id=escalation.id, topic=verdict.topic, importance=verdict.importance)
    return {"escalate": True, "id": escalation.id, "holding_line": escalation.holding_line,
            "timeout_s": rules.TIMEOUT_SECONDS, "poc_name": name}


@router.get("/{escalation_id}/status")
async def status(escalation_id: str, session: AsyncSession = Depends(get_session),
                 worker_token: Optional[str] = Depends(service_auth.worker_token_header)):
    e = await session.get(Escalation, escalation_id)
    meeting = await session.get(Meeting, e.meeting_id) if e and e.meeting_id else None
    if not e or not meeting:
        raise HTTPException(status_code=404, detail="No such escalation")
    service_auth.require_worker(worker_token, service_auth.meeting_scope(meeting.slug))
    name, company = "the team", False
    if meeting:
        _, name, company = await _poc(session, meeting)

    if e.status == EscalationStatus.OPEN.value and datetime.utcnow() >= e.expires_at:
        e.status = EscalationStatus.EXPIRED.value
        e.resolved_at = datetime.utcnow()
        session.add(e)
        await session.commit()
        log.info("escalation.expired", id=e.id)

    say = None
    if e.status == EscalationStatus.ANSWERED.value:
        say = rules.relay_line(name, e.answer or "", company)
    elif e.status in (EscalationStatus.EXPIRED.value, EscalationStatus.DECLINED.value):
        say = rules.apology_line(name, e.topic, e.question, company)
    return {"status": e.status, "say": say,
            "seconds_left": _read(e).seconds_left}


# ── the member ───────────────────────────────────────────────────────────

@router.get("/mine", response_model=list[EscalationRead])
async def mine(workspace: Workspace = Depends(get_current_workspace),
               session: AsyncSession = Depends(get_session),
               user: User = Depends(get_current_user)):
    rows = (await session.execute(select(Escalation).where(
        Escalation.workspace_id == workspace.id, Escalation.poc_user_id == user.id,
    ).order_by(Escalation.created_at.desc()).limit(30))).scalars().all()
    return [_read(e) for e in rows]


async def _own(session: AsyncSession, escalation_id: str, user: User) -> Escalation:
    e = await session.get(Escalation, escalation_id)
    # 404 for someone else's, same as a whisper thread: a colleague must not
    # be able to answer — or learn of — a question put to you.
    if not e or e.poc_user_id != user.id:
        raise HTTPException(status_code=404, detail="No such escalation")
    return e


@router.post("/{escalation_id}/answer", response_model=EscalationRead)
async def answer(escalation_id: str, body: AnswerBody,
                 session: AsyncSession = Depends(get_session),
                 user: User = Depends(get_current_user)):
    e = await _own(session, escalation_id, user)
    text = body.answer.strip()
    if not text:
        raise HTTPException(status_code=422, detail="An empty answer is not an answer — use decline")
    if e.status not in (EscalationStatus.OPEN.value, EscalationStatus.EXPIRED.value):
        raise HTTPException(status_code=409, detail=f"Already {e.status}")
    in_time = e.status == EscalationStatus.OPEN.value and datetime.utcnow() < e.expires_at
    e.answer = text[:1000]
    e.status = EscalationStatus.ANSWERED.value if in_time else EscalationStatus.ANSWERED_LATE.value
    e.resolved_at = datetime.utcnow()
    session.add(e)
    await session.commit()
    await session.refresh(e)
    return _read(e)


@router.post("/{escalation_id}/decline", response_model=EscalationRead)
async def decline(escalation_id: str, session: AsyncSession = Depends(get_session),
                  user: User = Depends(get_current_user)):
    e = await _own(session, escalation_id, user)
    if e.status != EscalationStatus.OPEN.value:
        raise HTTPException(status_code=409, detail=f"Already {e.status}")
    e.status = EscalationStatus.DECLINED.value
    e.resolved_at = datetime.utcnow()
    session.add(e)
    await session.commit()
    await session.refresh(e)
    return _read(e)


@router.get("/profile", response_model=ProfileBody)
async def get_profile(workspace: Workspace = Depends(get_current_workspace),
                      session: AsyncSession = Depends(get_session),
                      user: User = Depends(get_current_user)):
    p = await _profile(session, workspace.id, user.id)
    return ProfileBody(**(p.model_dump() if p else {}))


@router.put("/profile", response_model=ProfileBody)
async def put_profile(body: ProfileBody, workspace: Workspace = Depends(get_current_workspace),
                      session: AsyncSession = Depends(get_session),
                      user: User = Depends(get_current_user)):
    p = await _profile(session, workspace.id, user.id) or AgentProfile(workspace_id=workspace.id, user_id=user.id)
    p.display_name = (body.display_name or "").strip()[:40] or None
    p.always_escalate = [t.strip().lower() for t in body.always_escalate if t.strip()][:20]
    p.may_commit_to = (body.may_commit_to or "").strip()[:500] or None
    p.notes = (body.notes or "").strip()[:2000] or None
    p.updated_at = datetime.utcnow()
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return ProfileBody(**p.model_dump())


# ── Slack, best effort ───────────────────────────────────────────────────

async def _notify_slack(escalation_id: str) -> None:
    """A DM is the fastest way to reach someone who is not looking at the
    console. Needs the Slack app's chat:write scope; without it this logs
    and the console notification carries the whole weight."""
    from app.services.whisper.slack_surface import SlackSurfaceUnavailable, send_suggestion

    async with AsyncSessionLocal() as session:
        e = await session.get(Escalation, escalation_id)
        user = await session.get(User, e.poc_user_id) if e else None
        if not e or not user:
            return
        url = f"{get_settings().client_base_url.rstrip('/')}/me?tab=asked"
        text = (f"🔔 Your agent needs you on *{e.meeting_title or 'a call'}* — "
                f"reply within {rules.TIMEOUT_SECONDS}s: {url}")
        try:
            await send_suggestion(session, e.workspace_id, user, text, e.question, [])
        except SlackSurfaceUnavailable as exc:
            log.info("escalation.slack_unavailable", id=e.id, reason=str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — a DM must never break the call path
            log.warning("escalation.slack_failed", id=e.id, error=str(exc)[:200])
            return
        e.notified_via = list(e.notified_via or []) + ["slack"]
        session.add(e)
        await session.commit()
