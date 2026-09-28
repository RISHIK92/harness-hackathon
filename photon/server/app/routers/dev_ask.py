"""A dev-only way to ask the agent AS A REAL USER, without a token.

Why this exists: `POST /api/agent/ask` takes a client-asserted
`workspace_id` and nothing else, so exercising it by hand means first
finding a workspace uuid in the database, and even then the call does not
resolve the same way a real one does — a real call's persona and its
allowed tool list come from the workspace's connected sources, not from the
payload. Testing against `/api/agent/ask` with a hand-pasted uuid therefore
tests a configuration no real caller ever has.

This endpoint resolves a person the way the product does: an email (or the
only account on the box) -> their workspaces -> that workspace's real source
groups -> the exact `allowed_tools` a call would get. Then it calls the same
`answer_question()` everything else calls. Nothing is special-cased.

IT IMPERSONATES A USER WITH NO PASSWORD AND NO TOKEN. That is the whole
point, and it is why this router is mounted only under APP_ENV=development
AND ENABLE_DEV_IMPERSONATION=true (app/main.py; tests/test_dev_ask_gating.py).
Development alone is not enough: a development box can still be exposed
(scripts/dev.sh --with-ngrok). It must never be reachable from a deployment
holding anyone else's data.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agent.loop import answer_question
from app.config import get_settings
from app.database import get_session
from app.models import (
    Meeting,
    TranscriptEntry,
    TranscriptRole,
    User,
    Workspace,
    WorkspaceMember,
)
from app.services.meeting_slug import normalise
from app.services.tool_availability import default_enabled_keys, source_groups, tools_for

router = APIRouter()

# Conversation history, so a follow-up can be asked as its own curl instead
# of having to resend the whole conversation. Process-local and lost on
# reload — deliberately not a table: this is a scratchpad for testing, and a
# real call's history comes from the worker or the browser, never from here.
_SESSIONS: dict[str, list[dict]] = {}
_MAX_SESSION_TURNS = 12


class DevAskRequest(BaseModel):
    question: str
    # Who to act as. Falls back to the only account on this box.
    as_email: Optional[str] = None
    # Workspace id, or any case-insensitive substring of its name. Falls
    # back to this user's personal workspace.
    workspace: Optional[str] = None
    # Name a session and history accumulates under it across calls, so
    # follow-ups can be tested one curl at a time.
    session: Optional[str] = None
    # Forget this session's history before asking.
    reset: bool = False
    # Resolve the persona and allowed sources from a real meeting instead of
    # from the workspace defaults — the config a live call actually runs with.
    meeting_slug: Optional[str] = None
    language: Optional[str] = None
    # Write this exchange into the meeting's transcript, so a LATER call can
    # find it via search_past_calls. Off by default: a test question should
    # not silently become part of a real call's record.
    record_transcript: bool = False


# Reserved throwaway domains the test suites sign up under (tests/*.py use
# example.test; older runs used example.com). Filtered out of the "which
# account did you mean" list only — never auto-selected against, and if
# filtering leaves nothing the full list comes back, so a deployment whose
# only real account happens to live on one of these still works.
_FIXTURE_DOMAINS = ("@example.test", "@example.com")


async def _resolve_user(session: AsyncSession, email: Optional[str]) -> User:
    email = email or get_settings().dev_ask_email.strip() or None
    if email:
        user = (await session.execute(select(User).where(User.email == email))).scalars().first()
        if not user:
            raise HTTPException(404, f"no user with email {email!r}")
        return user

    users = (await session.execute(select(User).order_by(User.created_at))).scalars().all()
    if not users:
        raise HTTPException(404, "no users exist on this deployment yet — sign up first")
    if len(users) > 1:
        # Guessing which of several accounts is "me" would silently answer
        # from the wrong tenant's sources, which is the one mistake this
        # endpoint must not make quietly.
        real = [u for u in users if not u.email.endswith(_FIXTURE_DOMAINS)] or users
        if len(real) == 1:
            return real[0]
        raise HTTPException(
            400,
            {
                "error": "several accounts exist — say which one with as_email, "
                         "or set DEV_ASK_EMAIL in server/.env to stop being asked",
                "accounts": [u.email for u in real],
                "fixture_accounts_hidden": len(users) - len(real),
            },
        )
    return users[0]


async def _resolve_workspace(session: AsyncSession, user: User, wanted: Optional[str]) -> Workspace:
    rows = (
        await session.execute(
            select(Workspace)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user.id)
            .order_by(Workspace.created_at)
        )
    ).scalars().all()
    if not rows:
        raise HTTPException(404, f"{user.email} is not a member of any workspace")

    if wanted:
        for ws in rows:
            if ws.id == wanted:
                return ws
        matches = [ws for ws in rows if wanted.lower() in ws.name.lower()]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise HTTPException(404, {"error": f"no workspace matching {wanted!r}",
                                      "workspaces": [{"id": w.id, "name": w.name} for w in rows]})
        raise HTTPException(400, {"error": f"{wanted!r} matches several workspaces",
                                  "workspaces": [{"id": w.id, "name": w.name} for w in matches]})

    return next((ws for ws in rows if ws.is_personal), rows[0])


async def _config_for(session: AsyncSession, workspace: Workspace, meeting_slug: Optional[str]) -> dict:
    """The persona and tool list a real call would run with.

    With a meeting, this defers to the agent router's own resolver so the
    two cannot drift; without one it applies the workspace defaults, which
    is what the console does.
    """
    if meeting_slug:
        from sqlmodel import select

        from app.models import Meeting
        from app.routers.agent import _meeting_config

        meeting = (await session.execute(
            select(Meeting).where(Meeting.slug == normalise(meeting_slug)))).scalars().first()
        if meeting is None:
            raise HTTPException(404, f"no meeting with slug {normalise(meeting_slug)!r}")
        config = await _meeting_config(session, meeting)
        if config["workspace_id"] != workspace.id:
            raise HTTPException(
                403, f"meeting {normalise(meeting_slug)!r} belongs to a different workspace"
            )
        groups = await source_groups(session, workspace.id)
        return {**config, "groups": groups, "enabled": None}

    groups = await source_groups(session, workspace.id)
    enabled = default_enabled_keys(groups)
    return {
        "allowed_tools": set(tools_for(groups, enabled)),
        "bot_types": ["support"],
        "workspace_id": workspace.id,
        "meeting_slug": None,
        "org_name": None if workspace.is_personal else workspace.name,
        "agent_name": workspace.agent_name,
        "groups": groups,
        "enabled": enabled,
    }


def _sources_summary(config: dict) -> dict:
    groups = config["groups"]
    return {
        "available": [g.key for g in groups if g.available],
        "unavailable": [g.key for g in groups if not g.available],
        "enabled": config["enabled"] if config["enabled"] is not None else "(from the meeting)",
        "tools": sorted(config["allowed_tools"]),
    }


@router.get("/ask/whoami")
async def whoami(
    as_email: Optional[str] = None,
    workspace: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """Who /dev/ask would act as, and what that person's agent can reach.

    Worth its own endpoint: nearly every confusing answer from this thing is
    actually "it resolved a different workspace than you meant", or "that
    source isn't connected, so the tool was never offered to the planner" —
    both invisible in the answer itself, both obvious here.
    """
    user = await _resolve_user(session, as_email)
    ws = await _resolve_workspace(session, user, workspace)
    config = await _config_for(session, ws, None)
    all_ws = (
        await session.execute(
            select(Workspace)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user.id)
        )
    ).scalars().all()
    return {
        "as": {"email": user.email, "user_id": user.id},
        "workspace": {"id": ws.id, "name": ws.name, "is_personal": ws.is_personal,
                      "agent_name": ws.agent_name},
        "other_workspaces": [{"id": w.id, "name": w.name} for w in all_ws if w.id != ws.id],
        "sources": _sources_summary(config),
        "sessions": {k: len(v) for k, v in _SESSIONS.items()},
    }


@router.post("/ask")
async def dev_ask(payload: DevAskRequest, session: AsyncSession = Depends(get_session)):
    user = await _resolve_user(session, payload.as_email)
    ws = await _resolve_workspace(session, user, payload.workspace)
    config = await _config_for(session, ws, payload.meeting_slug)

    key = payload.session or ""
    if payload.reset:
        _SESSIONS.pop(key, None)
    history = list(_SESSIONS.get(key, [])) if key else []

    started = time.monotonic()
    result = await answer_question(
        payload.question,
        language=payload.language,
        workspace_id=config["workspace_id"],
        allowed_tools=config["allowed_tools"],
        bot_types=config["bot_types"],
        org_name=config["org_name"],
        agent_name=config["agent_name"],
        history=history,
        meeting_slug=config["meeting_slug"],
    )
    ms = int((time.monotonic() - started) * 1000)

    if key:
        turns = _SESSIONS.setdefault(key, [])
        turns.append({"role": "user", "text": payload.question})
        turns.append({"role": "agent", "text": result["answer"]})
        del turns[:-_MAX_SESSION_TURNS]

    recorded = False
    if payload.record_transcript and config["meeting_slug"]:
        meeting = (
            await session.execute(select(Meeting).where(Meeting.slug == config["meeting_slug"]))
        ).scalars().first()
        if meeting:
            session.add(TranscriptEntry(meeting_id=meeting.id, role=TranscriptRole.HUMAN,
                                        speaker_name=user.email, speaker_user_id=user.id,
                                        text=payload.question))
            session.add(TranscriptEntry(meeting_id=meeting.id, role=TranscriptRole.AGENT,
                                        speaker_name=config["agent_name"] or "Photon",
                                        text=result["answer"]))
            await session.commit()
            recorded = True

    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for call in result["tool_trace"]:
        for item in call.get("evidence") or []:
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            evidence.append({"id": item["id"], "source_type": item["source_type"],
                             "locator": item["locator"], "score": item["score"]})

    return {
        "as": user.email,
        "workspace": {"id": ws.id, "name": ws.name},
        "sources": _sources_summary(config),
        "session": payload.session,
        "history_sent": len(history),
        "ms": ms,
        # The readable half first — this is usually all you need from a curl.
        "answer": result["answer"],
        "confidence": result["confidence"],
        "abstained": result["abstained"],
        "escalation": result["escalation"],
        "tools_called": [{"tool": c["tool"], "args": c["args"], "ms": c["ms"],
                          "evidence": len(c.get("evidence") or [])}
                         for c in result["tool_trace"]],
        "evidence": evidence,
        "recorded_to_transcript": recorded,
        # …and the untouched Section 4 contract, for when the shape matters.
        "result": result,
    }


@router.delete("/ask/sessions")
async def clear_sessions(session_name: Optional[str] = None):
    if session_name:
        _SESSIONS.pop(session_name, None)
        return {"cleared": session_name}
    count = len(_SESSIONS)
    _SESSIONS.clear()
    return {"cleared_all": count}


__all__ = ["router", "get_settings"]
