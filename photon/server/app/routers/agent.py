from __future__ import annotations

import asyncio
import base64
import binascii
import json
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from fastapi import Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agent.loop import answer_question
from app.database import get_session
from app.models import Meeting, Workspace
from app.services.meeting_slug import normalise
from app.services.tool_availability import source_groups, tools_for


async def _call_config(session, payload) -> dict:
    """Persona + allowed tools for this request.

    Returns empty when no meeting is given, which keeps the text console and
    the tests working exactly as before (all tools, default persona).
    """
    if not payload.meeting_slug:
        return {}
    result = await session.execute(select(Meeting).where(Meeting.slug == normalise(payload.meeting_slug)))
    meeting = result.scalars().first()
    if not meeting:
        return {}
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

router = APIRouter()


class HistoryTurn(BaseModel):
    """One earlier turn of THIS conversation. `role` is "user" or "agent";
    anything else is coerced to "user" in app.agent.history."""
    role: str = "user"
    text: str = ""


class AgentAskRequest(BaseModel):
    question: str
    # The conversation so far, oldest first. Without it a follow-up ("why is
    # that?") has no referent at all. Client-asserted like workspace_id on
    # this still-unauthenticated route — but it is only ever read as prose
    # for the model to resolve a pronoun against, never as instructions, and
    # it cannot widen which tools run or which tenant is read: both of those
    # are resolved server-side from the meeting and forced onto every call.
    history: Optional[List[HistoryTurn]] = None
    repo_id: Optional[str] = None
    screen_context: Optional[str] = None
    screen_image_base64: Optional[str] = None  # a JPEG frame, base64-encoded
    language: Optional[str] = None  # BCP-47 (te-IN, ta-IN, hi-IN, en-IN) — answer in this
    # Which tenant's connected sources may be searched. Absent = the demo
    # corpus only, which is what keeps the seeded scenarios working. Only
    # consulted when repo_id is omitted — lets the loop disambiguate across
    # a workspace's repos instead of falling back to the single seed repo.
    # This endpoint is still unauthenticated (see CLAUDE.md's Phase 3
    # note), so workspace_id here is client-asserted, not verified against
    # a session — the same trust boundary as everything else on this route
    # today, not a new gap introduced by this field.
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
async def ask_stream(payload: AgentAskRequest, session: AsyncSession = Depends(get_session)):
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
    config = await _call_config(session, payload)
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
                workspace_id=config.get("workspace_id") or payload.workspace_id,
                allowed_tools=config.get("allowed_tools"),
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
    stream: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
):
    screen_image_bytes = _decode_frame(payload)
    config = await _call_config(session, payload)

    result = await answer_question(
        payload.question,
        payload.repo_id,
        payload.screen_context,
        screen_image_bytes,
        language=payload.language,
        workspace_id=config.get("workspace_id") or payload.workspace_id,
        allowed_tools=config.get("allowed_tools"),
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
