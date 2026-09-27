"""Cross-encoder reranking for code retrieval.

Dense-only retrieval over raw code is weak in two specific, measured ways
(see the numbers in CLAUDE.md's retrieval section):

1. It cannot tell a DEFINITION from a MENTION. `RETRY_BACKOFF_SECONDS` used
   to rank the docstring that references the constant above the line that
   defines it.
2. Test files outrank source. A test restates the identifiers in the
   question almost verbatim, so it embeds closer to the query than the
   implementation does — while being almost never the answer.

A cross-encoder reads query and chunk TOGETHER, so it can make both
distinctions. The pattern here is overfetch-then-rerank: pull a wider pool
from Qdrant by vector similarity (cheap, recall-oriented), then let the
reranker decide the final order (precise, but priced per document).

Deliberately FAIL-OPEN: if the rerank call errors or the key is missing,
the dense order is returned unchanged. A degraded ranking is a worse
answer; a raised exception is no answer at all, and this sits on the
critical path of a live call.
"""
from __future__ import annotations

import asyncio

import structlog

from app.config import get_settings

log = structlog.get_logger()


def _document_for(chunk: dict) -> str:
    """What the cross-encoder actually reads.

    The path and symbol name are prepended because they carry real signal
    the chunk body often doesn't — "tests/test_pricing.py" and
    "app/pricing.py" are the difference between a restatement and the
    answer, and neither is visible from the code text alone.
    """
    parts = [
        chunk.get("file_path") or "",
        chunk.get("symbol_name") or "",
        chunk.get("text") or "",
    ]
    return "\n".join(p for p in parts if p)


def _sync_rerank(query: str, chunks: list[dict], top_k: int) -> list[dict]:
    from app.core.embedding.embedder import get_voyage

    settings = get_settings()
    docs = [_document_for(c) for c in chunks]
    result = get_voyage().rerank(
        query=query,
        documents=docs,
        model=settings.rerank_model,
        top_k=min(top_k, len(chunks)),
    )
    ranked: list[dict] = []
    for item in result.results:
        chunk = dict(chunks[item.index])
        # The reranker's relevance score is the honest one to surface: it is
        # what actually decided this order. The dense score is kept under a
        # separate key rather than overwritten, so the two stay
        # distinguishable when debugging a bad ranking.
        chunk["_dense_score"] = chunk.get("_score")
        chunk["_score"] = float(item.relevance_score)
        ranked.append(chunk)
    return ranked


async def rerank_chunks(query: str, chunks: list[dict], top_k: int) -> list[dict]:
    """Rerank `chunks` against `query`, returning at most `top_k`.

    Returns the input truncated to top_k, unchanged, if reranking is
    disabled or fails.
    """
    settings = get_settings()
    if not settings.rerank_enabled or len(chunks) <= 1:
        return chunks[:top_k]

    try:
        return await asyncio.get_event_loop().run_in_executor(
            None, _sync_rerank, query, chunks, top_k
        )
    except Exception as exc:  # noqa: BLE001 - fail open, see module docstring
        log.warning("rerank.failed_open", error=str(exc), candidates=len(chunks))
        return chunks[:top_k]
