from __future__ import annotations

import asyncio
import base64
import binascii
import json
from typing import Any, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from fastapi import Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agent.loop import answer_question
from app.core import service_auth
from app.database import get_session
from app.models import Meeting, Repo, User, Workspace
from app.services.meeting_slug import normalise
from app.services.tool_availability import source_groups, tools_for


async def _meeting_config(session, meeting: Meeting) -> dict:
    """Persona + allowed tools for a turn inside one meeting."""
    workspace = await session.get(Workspace, meeting.workspace_id)
    groups = await source_groups(session, meeting.workspace_id)
    enabled = meeting.enabled_sources
    if enabled is None:
        from app.services.tool_availability import default_enabled_keys

        enabled = default_enabled_keys(groups)
    return {
        "allowed_tools": set(tools_for(groups, enabled)),
        "bot_types": meeting.bot_types or ["support"],
        "workspace_id": meeting.workspace_id,
        # The normalised slug, resolved from the DB rather than echoed from
        # the payload — it is what search_past_calls excludes, and an
        # unnormalised or unknown slug would exclude nothing.
        "meeting_slug": meeting.slug,
        # The agent introduces itself as this workspace's agent. A personal
        # workspace's auto-generated name ("rishik's workspace") is not a
        # company, so it is left unset rather than announced.
        "org_name": None if (workspace and workspace.is_personal) else (workspace.name if workspace else None),
        "agent_name": workspace.agent_name if workspace else None,
    }


async def _workspace_config(session, workspace: Workspace) -> dict:
    """A member asking outside any meeting: every source the workspace has
    actually connected, and nothing it has not. It used to be "all tools",
    which offered the planner tools for sources that do not exist here."""
    groups = await source_groups(session, workspace.id)
    return {
        "allowed_tools": set(tools_for(groups, [g.key for g in groups if g.available])),
        "workspace_id": workspace.id,
        "org_name": None if workspace.is_personal else workspace.name,
        "agent_name": workspace.agent_name,
    }


async def _user_from_bearer(session, authorization: Optional[str]) -> Optional[User]:
    """A full session token only. A scoped token (the Chrome extension's) is
    not a signed-in member and never reaches the agent from here."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    from jose import JWTError, jwt

    from app.config import get_settings

    settings = get_settings()
    try:
        claims = jwt.decode(authorization.split(" ", 1)[1], settings.jwt_secret_key,
                            algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    if claims.get("scope") or not claims.get("sub"):
        return None
    return await session.get(User, claims["sub"])


async def _resolve_turn(request: Request, payload: "AgentAskRequest", session,
                        authorization: Optional[str], worker_token: Optional[str]) -> dict:
    """Who is asking, and therefore which tenant, tools and persona apply.

    Two principals, nothing else:
      - the call-agent worker, with the token scoped to THIS meeting
        (app/core/service_auth.py) — the meeting decides everything;
      - a signed-in member — of the meeting's workspace when a meeting is
        named, else of the workspace they select.

    This route used to be open and took `workspace_id` (and `repo_id`) from
    the body, which made every tenant's sources one request away. The body's
    workspace_id is now only a *selection* among the caller's own
    memberships, and repo_id must belong to the resolved workspace.
    """
    from app.core.workspace import ensure_personal_workspace, membership_for

    slug = normalise(payload.meeting_slug) if payload.meeting_slug else None
    meeting = None
    if slug:
        meeting = (await session.execute(select(Meeting).where(Meeting.slug == slug))).scalars().first()

    if worker_token is not None:
        if meeting is None:
            raise HTTPException(status_code=404, detail="No meeting with that code")
        service_auth.require_worker(worker_token, service_auth.meeting_scope(meeting.slug))
        config = await _meeting_config(session, meeting)
    else:
        user = await _user_from_bearer(session, authorization)
        if user is None:
            raise HTTPException(status_code=401, detail="Sign in to ask Photon",
                                headers={"WWW-Authenticate": "Bearer"})
        if slug:
            # A member of the meeting's own workspace; anyone else (an
            # admitted guest, a signed-in outsider) asks out loud, where the
            # agent answers under the call's own configuration.
            if meeting is None or not await membership_for(session, meeting.workspace_id, user.id):
                raise HTTPException(status_code=404, detail="No meeting with that code")
            config = await _meeting_config(session, meeting)
        else:
            requested = request.headers.get("x-workspace-id") or payload.workspace_id
            if requested:
                if not await membership_for(session, requested, user.id):
                    raise HTTPException(status_code=404, detail="Workspace not found")
                workspace = await session.get(Workspace, requested)
                if workspace is None:
                    raise HTTPException(status_code=404, detail="Workspace not found")
            else:
                workspace = await ensure_personal_workspace(session, user)
            config = await _workspace_config(session, workspace)

    if payload.repo_id:
        repo = await session.get(Repo, payload.repo_id)
        if repo is None or repo.workspace_id != config["workspace_id"]:
            raise HTTPException(status_code=404, detail="Repo not found")
    return config


router = APIRouter()


class HistoryTurn(BaseModel):
    """One earlier turn of THIS conversation. `role` is "user" or "agent";
    anything else is coerced to "user" in app.agent.history."""
    role: str = "user"
    text: str = ""


class AgentAskRequest(BaseModel):
    question: str
    # The conversation so far, oldest first. Without it a follow-up ("why is
    # that?") has no referent at all. Client-supplied — but it is only ever
    # read as prose for the model to resolve a pronoun against, never as
    # instructions, and it cannot widen which tools run or which tenant is
    # read: both are resolved server-side from the principal (_resolve_turn).
    history: Optional[List[HistoryTurn]] = None
    repo_id: Optional[str] = None
    screen_context: Optional[str] = None
    screen_image_base64: Optional[str] = None  # a JPEG frame, base64-encoded
    language: Optional[str] = None  # BCP-47 (te-IN, ta-IN, hi-IN, en-IN) — answer in this
    # Which of the caller's OWN workspaces to answer from, when no meeting is
    # named (the X-Workspace-Id header wins). A selection, never a grant:
    # _resolve_turn 404s unless the signed-in caller is a member.
    workspace_id: Optional[str] = None
    # When present, the call's own configuration decides the persona and
    # which sources may be used. Resolved server-side rather than trusted
    # from the caller: the worker should not be able to widen a call's
    # source list by sending a different payload.
    meeting_slug: Optional[str] = None


def _decode_frame(payload: "AgentAskRequest") -> Optional[bytes]:
    if not payload.screen_image_base64:
        return None
    try:
        return base64.b64decode(payload.screen_image_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="screen_image_base64 is not valid base64")


@router.post("/ask/stream")
async def ask_stream(
    payload: AgentAskRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(default=None),
    worker_token: Optional[str] = Depends(service_auth.worker_token_header),
):
    """Server-sent events for one turn, emitted AS IT HAPPENS: plan.start,
    tool.start/tool.done (with per-tool ms), compose, verify, turn.done.

    Distinct from `/ask?stream=true`, which runs the whole turn to
    completion first and only then chunks the finished answer word by word
    — useless for latency tracking, since nothing is sent until everything
    is already over. Here the loop pushes events into a queue while it
    runs and this generator drains them, so a client sees which tool is
    running while it's still running.
    """
    screen_image_bytes = _decode_frame(payload)
    config = await _resolve_turn(request, payload, session, authorization, worker_token)
    queue: asyncio.Queue[Optional[dict[str, Any]]] = asyncio.Queue()

    def sink(event: dict[str, Any]) -> None:
        # Called from inside the agent loop's own task; put_nowait keeps
        # the sink synchronous and non-blocking so tracing can never stall
        # or reorder the work it's tracing (the queue is unbounded).
        queue.put_nowait(event)

    async def run() -> None:
        try:
            await answer_question(
                payload.question,
                payload.repo_id,
                payload.screen_context,
                screen_image_bytes,
                on_event=sink,
                language=payload.language,
                workspace_id=config["workspace_id"],
                allowed_tools=config["allowed_tools"],
                bot_types=config.get("bot_types"),
                org_name=config.get("org_name"),
                agent_name=config.get("agent_name"),
                history=payload.history,
                meeting_slug=config.get("meeting_slug"),
            )
        except Exception as exc:  # noqa: BLE001 - report the failure to the client, don't hang it
            queue.put_nowait({"type": "turn.error", "t": 0, "seq": 0, "error": str(exc)})
        finally:
            queue.put_nowait(None)  # sentinel: the turn is over either way

    async def gen():
        task = asyncio.create_task(run())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            # A client that disconnects mid-turn (closed the tab, left the
            # call) must not leave the turn running forever behind it.
            if not task.done():
                task.cancel()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/ask")
async def ask(
    payload: AgentAskRequest,
    request: Request,
    stream: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(default=None),
    worker_token: Optional[str] = Depends(service_auth.worker_token_header),
):
    screen_image_bytes = _decode_frame(payload)
    config = await _resolve_turn(request, payload, session, authorization, worker_token)

    result = await answer_question(
        payload.question,
        payload.repo_id,
        payload.screen_context,
        screen_image_bytes,
        language=payload.language,
        workspace_id=config["workspace_id"],
        allowed_tools=config["allowed_tools"],
        bot_types=config.get("bot_types"),
        org_name=config.get("org_name"),
        agent_name=config.get("agent_name"),
        history=payload.history,
        meeting_slug=config.get("meeting_slug"),
    )

    if not stream:
        return result

    async def gen():
        # answer_question composes the full answer before verification can run
        # (claims must be checked against the complete evidence set), so this
        # isn't a true Gemini token stream — it's the verified answer chunked
        # word-by-word over SSE, reusing the same wire format/headers as
        # routers/query.py's _stream_question for a consistent client story.
        yield f"data: {json.dumps({'type': 'meta', 'tool_trace': result['tool_trace'], 'confidence': result['confidence'], 'abstained': result['abstained']})}\n\n"
        for word in result["answer"].split(" "):
            yield f"data: {json.dumps({'type': 'token', 'text': word + ' '})}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'result': result})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
