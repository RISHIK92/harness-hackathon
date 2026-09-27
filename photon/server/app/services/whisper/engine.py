"""When to whisper, and to whom.

Whisper mode inverts the speak-mode trade-off. On a live call the agent's
mistake is heard by the customer, so the bar for answering is high. Here
nobody but the team sees a suggestion, so a useless one costs a glance —
while a MISSED one costs the thing whisper exists for. The gate is therefore
looser than `small_talk.classify()`, and deliberately so.

It is still a gate. Firing the full pipeline on every line of a meeting
would be one LLM turn per sentence, most of them for "yeah", "right",
"sorry, go ahead".
"""
from __future__ import annotations

import asyncio
import re

import structlog
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agent.loop import answer_question
from app.models import (
    WhisperLine,
    WhisperMessage,
    WhisperRole,
    WhisperSession,
    WhisperThread,
    WorkspaceMember,
)
from app.services.tool_availability import default_enabled_keys, source_groups, tools_for
from app.services.whisper.warnings import warnings_for

log = structlog.get_logger()

# How many earlier lines of the MEETING travel with a question, so "why is
# that?" from the client resolves the same way it does in speak mode.
CONTEXT_LINES = 6

_QUESTION_MARK = re.compile(r"\?\s*$")
_INTERROGATIVE = re.compile(
    r"\b(who|what|when|where|why|how|which|can|could|do|does|did|is|are|was|were|will|would|"
    r"should|have|has|any|is there|are there)\b",
    re.I,
)
# Asks that carry no question word at all. "we need X", "it's still broken",
# "send me the docs" — all things a rep has to answer.
_IMPLICIT_ASK = re.compile(
    r"\b(we need|i need|we want|looking for|still (?:broken|failing|not working)|doesn't work|"
    r"does not work|can you send|send me|walk me through|show me|explain|tell me about|"
    r"wondering (?:if|about)|curious (?:if|about)|not sure (?:if|why|how))\b",
    re.I,
)
# Pure acknowledgement. Cheap to skip and by far the most common line.
_BACKCHANNEL = re.compile(
    r"^\s*(yeah|yep|yes|no|nope|ok|okay|right|sure|got it|mm+|uh huh|thanks|thank you|"
    r"perfect|great|cool|exactly|sorry|go ahead|makes sense)[\s.!,]*$",
    re.I,
)
MIN_WORDS = 3


def should_suggest(text: str) -> bool:
    """Whether this client line deserves an unprompted answer."""
    line = (text or "").strip()
    if len(line.split()) < MIN_WORDS or _BACKCHANNEL.match(line):
        return False
    return bool(_QUESTION_MARK.search(line) or _INTERROGATIVE.search(line) or _IMPLICIT_ASK.search(line))


async def _threads_for_members(session: AsyncSession, whisper: WhisperSession) -> list[WhisperThread]:
    """Every member's thread, created on demand.

    Created here rather than when a member opens the UI, so somebody who
    joins the call late still has the suggestions that fired before they
    looked — the alternative is a thread that starts at whenever you happened
    to click.
    """
    members = (await session.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == whisper.workspace_id)
    )).scalars().all()
    existing = {
        t.user_id: t
        for t in (await session.execute(
            select(WhisperThread).where(WhisperThread.session_id == whisper.id)
        )).scalars().all()
    }
    threads = []
    for member in members:
        thread = existing.get(member.user_id)
        if thread is None:
            thread = WhisperThread(
                session_id=whisper.id, workspace_id=whisper.workspace_id, user_id=member.user_id
            )
            session.add(thread)
            await session.flush()
        threads.append(thread)
    return threads


async def _meeting_context(session: AsyncSession, session_id: str, before_id: str) -> list[dict]:
    """Recent meeting lines as agent history, oldest first."""
    rows = (await session.execute(
        select(WhisperLine)
        .where(WhisperLine.session_id == session_id, WhisperLine.id != before_id)
        .order_by(WhisperLine.created_at.desc())
        .limit(CONTEXT_LINES)
    )).scalars().all()
    return [
        # Everything said in the room is "user" from the agent's point of
        # view — Photon is not a participant in a whispered meeting, it is
        # reading over a shoulder. Labelling our own side as "agent" would
        # tell the model it had already spoken, which it has not.
        {"role": "user", "text": f"{r.speaker_name}: {r.text}"}
        for r in reversed(rows)
    ]


async def suggest_for_line(session: AsyncSession, whisper: WhisperSession, line: WhisperLine) -> int:
    """Answer a client question and drop it into every member's thread.

    Returns how many threads it reached. Failures are swallowed: a whisper
    is an assist, and it must never break transcript ingestion, which is the
    part that has to keep working.
    """
    groups = await source_groups(session, whisper.workspace_id)
    allowed = set(tools_for(groups, default_enabled_keys(groups)))
    if not allowed:
        log.info("whisper.no_sources", session_id=whisper.id)
        return 0

    history = await _meeting_context(session, whisper.id, line.id)
    try:
        answer = await answer_question(
            line.text,
            workspace_id=whisper.workspace_id,
            allowed_tools=allowed,
            history=history,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("whisper.answer_failed", error=str(exc), session_id=whisper.id)
        return 0

    evidence = [e for call in answer.get("tool_trace") or [] for e in (call.get("evidence") or [])]
    warnings = warnings_for(answer)
    threads = await _threads_for_members(session, whisper)
    for thread in threads:
        session.add(WhisperMessage(
            thread_id=thread.id,
            role=WhisperRole.SUGGESTION,
            text=answer.get("answer") or "",
            trigger_text=line.text,
            evidence=evidence,
            warnings=warnings,
            confidence=answer.get("confidence"),
            abstained=bool(answer.get("abstained")),
        ))
    await session.commit()
    log.info("whisper.suggested", session_id=whisper.id, threads=len(threads),
             warnings=[w["code"] for w in warnings], abstained=answer.get("abstained"))
    return len(threads)


# Background suggestions. Held here so a running task is never garbage
# collected mid-turn (asyncio keeps only a weak reference to it).
_PENDING: set[asyncio.Task] = set()


def schedule_suggestion(whisper_id: str, line_id: str) -> None:
    """Answer a client line WITHOUT holding up whoever delivered it.

    Every ingest path is latency-sensitive in a different way: Recall retries
    a webhook that is slow to acknowledge, and the call worker's transcript
    write sits on its own turn path. The suggestion takes a full agent turn
    (~2-3s), so it runs after the line is stored, in its own DB session.
    """
    task = asyncio.create_task(_suggest_later(whisper_id, line_id))
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


async def _suggest_later(whisper_id: str, line_id: str) -> None:
    from app.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            whisper = await session.get(WhisperSession, whisper_id)
            line = await session.get(WhisperLine, line_id)
            if whisper and line:
                await suggest_for_line(session, whisper, line)
    except Exception as exc:  # noqa: BLE001
        log.error("whisper.background_suggest_failed", error=str(exc), session_id=whisper_id)


async def answer_in_thread(
    session: AsyncSession, whisper: WhisperSession, thread: WhisperThread, question: str
) -> WhisperMessage:
    """A member asking Photon privately, mid-call. Same loop, same rules."""
    groups = await source_groups(session, whisper.workspace_id)
    allowed = set(tools_for(groups, default_enabled_keys(groups)))

    prior = (await session.execute(
        select(WhisperMessage)
        .where(WhisperMessage.thread_id == thread.id)
        .order_by(WhisperMessage.created_at.desc())
        .limit(CONTEXT_LINES)
    )).scalars().all()
    history = [
        {"role": "user" if m.role == WhisperRole.MEMBER else "agent", "text": m.text}
        for m in reversed(prior)
    ]

    session.add(WhisperMessage(thread_id=thread.id, role=WhisperRole.MEMBER, text=question))
    answer = await answer_question(
        question, workspace_id=whisper.workspace_id, allowed_tools=allowed, history=history
    )
    evidence = [e for call in answer.get("tool_trace") or [] for e in (call.get("evidence") or [])]
    message = WhisperMessage(
        thread_id=thread.id,
        role=WhisperRole.AGENT,
        text=answer.get("answer") or "",
        evidence=evidence,
        warnings=warnings_for(answer),
        confidence=answer.get("confidence"),
        abstained=bool(answer.get("abstained")),
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message
