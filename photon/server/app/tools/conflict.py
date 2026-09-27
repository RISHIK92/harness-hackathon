"""check_conflict — the S3 tool. Retrieves the top doc and code evidence for
a claim and asks the LLM one narrow question: do these two sources agree?
The LLM's job is kept small deliberately (see build plan Phase 2) — broad
conflict detection doesn't work reliably, this narrow version does.

Still returns the standard {tool, status, evidence, note} shape (Section 4
contract has no exceptions) — the agrees/conflicts/insufficient verdict and
the LLM's one-line reasoning are folded into `note`, not a new top-level key.
"""
from __future__ import annotations

import asyncio

import structlog

from app.core.llm.openrouter import sync_chat
from app.tools.code import search_code
from app.tools.evidence import tool_error, tool_result
from app.tools.knowledge import search_docs

log = structlog.get_logger()

_PROMPT = """You are checking exactly one thing: do these two sources assert \
the SAME fact about the claim below? Do not evaluate correctness, only agreement.

Claim: {claim}

Source A (docs):
{doc_text}

Source B (code):
{code_text}

Respond with exactly two lines and nothing else:
Line 1: one word — agrees, conflicts, or insufficient
Line 2: under 20 words, citing the specific numbers/facts that agree or disagree."""


async def check_conflict(claim: str, repo_id: str | None = None) -> dict:
    if not repo_id:
        return tool_error("check_conflict", "which repository? specify one of the known repos for this workspace")
    docs_result = await search_docs(claim, top_k=1)
    code_result = await search_code(claim, repo_id, top_k=1)

    doc_ev = docs_result["evidence"][0] if docs_result["status"] == "ok" else None
    code_ev = code_result["evidence"][0] if code_result["status"] == "ok" else None

    if not doc_ev or not code_ev:
        evidence = [e for e in (doc_ev, code_ev) if e]
        missing = "docs" if not doc_ev else "code"
        return tool_result(
            "check_conflict", evidence, note=f"insufficient: no {missing} evidence found for this claim"
        )

    prompt = _PROMPT.format(claim=claim, doc_text=doc_ev["snippet"], code_text=code_ev["snippet"])
    try:
        raw = await asyncio.get_event_loop().run_in_executor(None, sync_chat, prompt, 300, 0.0)
    except Exception as exc:  # noqa: BLE001
        log.error("tool.check_conflict_llm_error", error=str(exc))
        return tool_error("check_conflict", f"LLM judge call failed: {exc}")

    lines = [l.strip() for l in raw.splitlines() if l.strip()] if raw else []
    verdict = lines[0].lower() if lines else "insufficient"
    if verdict not in ("agrees", "conflicts", "insufficient"):
        verdict = "insufficient"
    reason = lines[1] if len(lines) > 1 else raw

    return tool_result("check_conflict", [doc_ev, code_ev], note=f"{verdict}: {reason}")
