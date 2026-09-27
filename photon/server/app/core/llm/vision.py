"""Screen-frame vision analysis. Was originally direct-Gemini-SDK
(gemini_vision.py) but that hit the exact same free-tier quota wall as
Gemini text generation did in Phase 3 — confirmed directly (ResourceExhausted
after a handful of test calls). Moved to OpenRouter (openrouter.py's
sync_chat_vision), same google/gemini-3.7-flash model, just billed through
OpenRouter instead of a Gemini API key.

Called from the agent loop when a turn arrives with a screen frame
attached; the description this produces becomes a citable "screen"
source_type evidence item (see app.agent.loop), not raw unverifiable
prose fed straight to the compose LLM — Section 4's "no uncited claim"
rule applies to what's on screen the same as everything else.
"""
from __future__ import annotations

import asyncio

import structlog

from app.core.llm.openrouter import sync_chat_vision

log = structlog.get_logger()

_PROMPT = """You are looking at a screen-share frame from a live customer support call. \
The customer just asked: {question}

Describe only what's literally visible in the image that's relevant to their question — \
UI elements, text, buttons, error messages, field values. Be specific (exact labels/text you \
can read) and brief (2-4 sentences). If the image doesn't show anything relevant to the \
question, say so plainly. Do not guess at anything outside the frame."""


async def describe_screen(image_bytes: bytes, question: str) -> str | None:
    """Returns a text description, or None if the vision call fails —
    callers must treat None as "no visual context available", never
    fabricate a description in its place."""
    prompt = _PROMPT.format(question=question)
    try:
        return await asyncio.get_event_loop().run_in_executor(
            None, sync_chat_vision, prompt, image_bytes, 300, 0.1
        )
    except Exception as exc:  # noqa: BLE001 - a vision-call failure must not take the answer down
        log.error("vision.describe_error", error=str(exc))
        return None
