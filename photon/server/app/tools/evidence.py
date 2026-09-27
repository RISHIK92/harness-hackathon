"""The tool contract (see CLAUDE.md Section 4). Every tool in app.tools
returns a dict built by tool_result()/tool_error() and every evidence item
is built by make_evidence() — this is the one place locator/id formatting
lives, so it can't drift between tools.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

VALID_SOURCE_TYPES = {
    "code",
    "docs",
    "ticket",
    "slack",
    "account",
    "log",
    "commit",
    "pr",
    "incident",
    "screen",  # a live screen-share frame description (app.agent.loop), not corpus evidence
    "call",  # a line from an EARLIER call's transcript (app.tools.memory)
}


def _make_id(source_type: str, locator: str) -> str:
    h = hashlib.sha1(f"{source_type}:{locator}".encode()).hexdigest()[:8]
    return f"ev_{h}"


# How much of a retrieved chunk survives into the evidence the compose step
# actually reads. This was 800, which was invisible against the Python seed
# corpus (median chunk 193 chars, 3% affected) and catastrophic against a
# real TypeScript repo (median 2620 chars, 80% of chunks truncated). The
# measured symptom: search_code ranked the chunk defining MAX_RETRIES = 4
# first, the constant sat on line 23, the cap cut at ~line 20, and the agent
# abstained on a question it had already retrieved the answer to.
SNIPPET_MAX_CHARS = 2400  # override with EVIDENCE_SNIPPET_MAX_CHARS


def _snippet_cap() -> int:
    from app.config import get_settings

    return get_settings().evidence_snippet_max_chars


def make_evidence(source_type: str, locator: str, snippet: str, score: float = 1.0) -> dict:
    assert source_type in VALID_SOURCE_TYPES, f"unknown source_type {source_type!r}"
    text = snippet or ""
    cap = _snippet_cap()
    if len(text) > cap:
        # Truncation must be VISIBLE. Silently handing the model the first
        # N characters of a range whose locator still claims every line is
        # how a well-retrieved fact turns into an abstention, with nothing
        # in the trace to say why.
        text = text[:cap] + "\n… [snippet truncated]"
    return {
        "id": _make_id(source_type, locator),
        "source_type": source_type,
        "locator": locator,
        "snippet": text,
        "score": round(float(score), 4),
        "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def tool_result(tool: str, evidence: list[dict], note: str | None = None) -> dict:
    status = "ok" if evidence else "empty"
    return {
        "tool": tool,
        "status": status,
        "evidence": evidence,
        "note": note if note is not None else (None if evidence else "no evidence found"),
    }


def tool_error(tool: str, note: str) -> dict:
    return {"tool": tool, "status": "error", "evidence": [], "note": note}
