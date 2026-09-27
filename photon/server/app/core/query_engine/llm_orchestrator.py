from __future__ import annotations
from typing import AsyncIterator

from app.core.llm.openrouter import stream_chat


async def stream_answer(prompt: str, question: str) -> AsyncIterator[str]:
    """Stream tokens for the web console's /api/query. See app.config.Settings
    for why this moved off Gemini (20 requests/day free-tier cap) onto
    OpenRouter."""
    async for token in stream_chat(prompt):
        yield token
