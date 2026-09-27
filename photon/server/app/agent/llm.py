"""Text generation for the agent loop's plan/compose calls, plus a tolerant
JSON extractor since the model doesn't always honor "no markdown fences"
cleanly even with json_mode on. Zero transport imports — this only talks
to OpenRouter's HTTP API via app.core.llm.openrouter.
"""
from __future__ import annotations

import asyncio
import json
import re

import structlog

from app.core.llm.openrouter import sync_chat

log = structlog.get_logger()


async def generate(
    prompt: str,
    max_output_tokens: int = 1500,
    temperature: float = 0.1,
    json_mode: bool = False,
    json_schema: dict | None = None,
) -> str:
    return await asyncio.get_event_loop().run_in_executor(
        None, sync_chat, prompt, max_output_tokens, temperature, json_mode, json_schema
    )


# Grammars for the two calls the agent makes. `strict` is False deliberately —
# see sync_chat's docstring: strict mode cannot express the planner's
# free-form `args` and empties it out.
PLAN_SCHEMA = {
    "name": "plan",
    "strict": False,
    "schema": {
        "type": "object",
        "properties": {
            "calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string"},
                        "args": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["tool", "args"],
                },
            }
        },
        "required": ["calls"],
    },
}

ANSWER_SCHEMA = {
    "name": "answer",
    "strict": False,
    "schema": {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["text", "evidence_ids"],
                },
            },
            "abstained": {"type": "boolean"},
            "escalation": {"type": ["string", "null"]},
        },
        "required": ["answer", "claims", "abstained"],
    },
}


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _scan_balanced(text: str, start: int) -> str | None:
    """The substring from `start` through the bracket that closes it.

    Replaces a greedy `\{.*\}` regex, which ran to the LAST brace in the
    string and so swallowed whatever junk followed the object — the exact
    failure being recovered from here (`…}]}}`). Strings and escapes are
    tracked, so a brace inside a query value cannot end the scan early.
    """
    opener = text[start]
    closer = {"{": "}", "[": "]"}.get(opener)
    if closer is None:
        return None
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None  # never closed — truncated output


def _salvage_calls(text: str) -> dict | None:
    """Recover a plan whose `calls` ARRAY is intact but whose wrapper is not.

    Every malformed planner output collected from real eval runs had this
    shape: a perfectly valid array followed by a stray token welded on where
    the closing brace belonged — `]5}`, `] K}`, `]absolut}`, `]uretics}`, or
    simply nothing at all because the output was cut off. The array is the
    part that carries the decision, so it is worth recovering; abstaining
    because of a trailing character the model hallucinated is a worse answer
    for a reason the caller can never see.
    """
    key = text.find('"calls"')
    if key == -1:
        return None
    calls: list = []
    cursor = text.find("[", key)
    while cursor != -1:
        block = _scan_balanced(text, cursor)
        if block is None:
            break
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            break
        # A second array is the other observed corruption — `{"calls": [A], [B]}`
        # — so keep going, but only accept something that really is a list of
        # tool calls rather than an array that happened to appear in an
        # argument value.
        if isinstance(parsed, list) and all(isinstance(c, dict) and "tool" in c for c in parsed):
            calls.extend(parsed)
        cursor = text.find("[", cursor + len(block))
    return {"calls": calls} if calls else None


def extract_json(text: str) -> dict | None:
    if not text:
        return None
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start != -1:
        block = _scan_balanced(cleaned, start)
        if block:
            try:
                return json.loads(block)
            except json.JSONDecodeError:
                pass

    salvaged = _salvage_calls(cleaned)
    if salvaged:
        log.warning("agent.llm_json_salvaged", calls=len(salvaged["calls"]), text=cleaned[-80:])
        return salvaged

    if len(cleaned) < 10:
        # Not a parsing problem: the provider returned a bare "{" (or
        # nothing) and stopped. Logged distinctly so it is not mistaken for
        # malformed output — the fix for that class is the grammar, and there
        # is nothing here to repair. The caller's empty-plan retry covers it;
        # observed ~2 times per 24-turn eval run and not reproducible on
        # demand (0/10 when probed directly).
        log.warning("agent.llm_empty_generation", text=cleaned)
        return None
    log.warning("agent.llm_json_extract_failed", text=cleaned[:300])
    return None
