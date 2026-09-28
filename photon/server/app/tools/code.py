"""Code tools — thin JSON wrappers around YASML's existing retrieval
internals (app.core.embedding.embedder, app.core.graph.builder,
app.core.query_engine.*). No prose leaves this layer; the LLM composition
step lives in app.agent, not here.
"""
from __future__ import annotations

import os

import structlog

from app.config import get_settings
from app.core.embedding.embedder import vector_search
from app.core.graph.builder import Neo4jClient
from app.core.query_engine.retrieval import hybrid_retrieve
from app.core.retrieval.rerank import rerank_chunks
from app.models import QueryIntent
from app.tools.evidence import make_evidence, tool_error, tool_result

log = structlog.get_logger()


def _has_content(text: str) -> bool:
    """Mirror of the chunker's guard, applied at read time.

    Same threshold deliberately: a chunk the chunker would refuse to create
    today should not be returned from a corpus ingested before it refused.
    """
    return sum(c.isalnum() for c in text or "") >= 6


def _overclaims_its_range(chunk: dict) -> bool:
    """A locator that claims far more lines than its text holds.

    The standing rule is that a locator is never fabricated, and one that
    says L1-L1315 over 72 lines of text is exactly that: the code panel reads
    the file back by that range and shows 1315 lines as "what the answer
    cited". Found live in drillController.ts — a survivor of the chunker that
    predates _contiguous_runs, still in Qdrant because a re-ingest overwrote
    chunk ids 0..n and orphaned the rest rather than replacing the set.

    Ingest now purges a repo's chunks first, so no new ones appear; this
    keeps the ones already embedded out of an answer without demanding a
    re-ingest of every repo. Tolerant by design — a chunk whose text is a
    line or two short of its range is normal, and only a gross mismatch is
    a fabricated locator.
    """
    start, end = chunk.get("start_line"), chunk.get("end_line")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    claimed = end - start + 1
    actual = (chunk.get("text") or "").count("\n") + 1
    return claimed > max(actual * 2, actual + 20)


def _rank_score(index: int, total: int) -> float:
    """Fallback only. Retrieval now carries a real score (`_score`: cosine
    from Qdrant, or the reranker's relevance when reranking ran), so this
    is used only for results that never came from a scored search — graph
    nodes and the like."""
    if total <= 1:
        return 1.0
    return round(1.0 - (index / total) * 0.5, 4)


def _chunk_to_evidence(chunk: dict, index: int, total: int) -> dict:
    file_path = chunk.get("file_path") or chunk.get("path") or "?"
    start = chunk.get("start_line")
    end = chunk.get("end_line")
    locator = f"{file_path}:L{start}-L{end}" if start is not None else file_path
    score = chunk.get("_score")
    score = round(float(score), 4) if score is not None else _rank_score(index, total)
    return make_evidence("code", locator, chunk.get("text", ""), score)


async def _retrieve_ranked(
    query: str, repo_id: str | None, workspace_id: str | None, top_k: int
) -> list[dict]:
    """Overfetch by vector similarity, then rerank down to top_k.

    Recall is cheap and precision is what the answer depends on, so the
    pool is deliberately wider than top_k. `rerank_chunks` fails open, so
    a reranker outage degrades this to plain dense search rather than
    failing the tool.
    """
    pool = max(top_k, get_settings().rerank_pool)
    chunks = await vector_search(repo_id, query, top_k=pool, workspace_id=workspace_id)
    # Dropped BEFORE reranking, not after: a junk chunk that survives into the
    # pool costs one of the reranker's slots and can still out-rank a real hit
    # on a short query. The chunker no longer creates these (see its
    # _has_content note), but ~27 are already embedded in this deployment's
    # 61k and re-ingesting every repo to evict them is not worth it — so they
    # are filtered on the way out too.
    chunks = [c for c in chunks if _has_content(c.get("text", ""))]
    chunks = [c for c in chunks if not _overclaims_its_range(c)]
    return await rerank_chunks(query, chunks, top_k)


def _graph_node_to_evidence(node: dict, score: float = 0.55) -> dict:
    path = node.get("path") or node.get("id") or "?"
    snippet_bits = []
    for key in ("language", "sym_count", "top_symbols", "fan_out"):
        if key in node and node[key] not in (None, [], 0):
            snippet_bits.append(f"{key}={node[key]}")
    snippet = f"module {path}" + (f" ({', '.join(snippet_bits)})" if snippet_bits else "")
    return make_evidence("code", path, snippet, score)


async def search_code(query: str, repo_id: str | None = None, workspace_id: str | None = None, top_k: int = 8) -> dict:
    # Cross-repo mode: no specific repo named, so search every repo in the
    # workspace and let relevance ranking sort out which repo's chunks
    # actually answer the question (app.agent.loop's multi-repo
    # disambiguation — see CLAUDE.md). Chunks are tagged with workspace_id
    # at ingest time (app.tasks.ingestion); repos ingested before that
    # change have no workspace_id payload and won't be found this way.
    if not repo_id and not workspace_id:
        return tool_error("search_code", "no repo_id or workspace_id given — nothing to search")
    try:
        chunks = await _retrieve_ranked(query, repo_id, workspace_id, top_k)
    except Exception as exc:  # noqa: BLE001 - surfaced as a typed tool error, not a 500
        log.error("tool.search_code_error", error=str(exc))
        return tool_error("search_code", f"search_code failed: {exc}")

    evidence = [_chunk_to_evidence(c, i, len(chunks)) for i, c in enumerate(chunks)]
    return tool_result("search_code", evidence, note=None if evidence else f"no code matched '{query}'")


async def trace_symbol(symbol: str, repo_id: str | None = None) -> dict:
    if not repo_id:
        # Unlike search_code/find_usages, this walks the Neo4j module graph,
        # which is per-repo — there's no cross-repo graph to fall back to.
        # The loop only leaves repo_id unset here when the planner's guess
        # didn't match a real repo in the workspace, so ask it to narrow
        # down rather than guessing which repo's graph to walk.
        return tool_error("trace_symbol", "which repository? specify one of the known repos for this workspace")
    try:
        chunks, graph_nodes = await hybrid_retrieve(
            repo_id=repo_id, question=f"what calls or is called by {symbol}", intent=QueryIntent.RELATIONAL
        )
    except Exception as exc:  # noqa: BLE001
        log.error("tool.trace_symbol_error", error=str(exc))
        return tool_error("trace_symbol", f"trace_symbol failed: {exc}")

    evidence = [_chunk_to_evidence(c, i, len(chunks)) for i, c in enumerate(chunks)]
    evidence += [_graph_node_to_evidence(n) for n in graph_nodes]
    return tool_result(
        "trace_symbol", evidence, note=None if evidence else f"no code or graph evidence for symbol '{symbol}'"
    )


async def find_usages(symbol: str, repo_id: str | None = None, workspace_id: str | None = None) -> dict:
    if not repo_id and not workspace_id:
        return tool_error("find_usages", "no repo_id or workspace_id given — nothing to search")
    try:
        chunks = await _retrieve_ranked(
            f"usages and references of {symbol}", repo_id, workspace_id, top_k=10
        )
    except Exception as exc:  # noqa: BLE001
        log.error("tool.find_usages_error", error=str(exc))
        return tool_error("find_usages", f"find_usages failed: {exc}")

    evidence = [_chunk_to_evidence(c, i, len(chunks)) for i, c in enumerate(chunks)]
    return tool_result("find_usages", evidence, note=None if evidence else f"no usages found for '{symbol}'")


async def read_file(path: str, repo_id: str | None = None, start: int | None = None, end: int | None = None) -> dict:
    if not repo_id:
        return tool_error("read_file", "which repository? specify one of the known repos for this workspace")

    from sqlmodel import Session, create_engine

    from app.config import get_settings
    from app.models import Repo

    settings = get_settings()
    engine = create_engine(settings.sync_database_url)
    with Session(engine) as session:
        repo = session.get(Repo, repo_id)

    if not repo or not repo.local_path:
        return tool_error("read_file", f"repo {repo_id} not found or not ingested")

    full_path = os.path.join(repo.local_path, path.lstrip("/"))
    repo_root = os.path.realpath(repo.local_path)
    real = os.path.realpath(full_path)
    if real != repo_root and not real.startswith(repo_root + os.sep):
        return tool_error("read_file", "path traversal denied")
    if not os.path.isfile(full_path):
        return tool_result("read_file", [], note=f"file not found: {path}")

    with open(full_path, "r", errors="replace") as f:
        lines = f.readlines()

    start_line = start or 1
    end_line = end or len(lines)
    snippet = "".join(lines[start_line - 1 : end_line])
    locator = f"{path}:L{start_line}-L{end_line}"
    return tool_result("read_file", [make_evidence("code", locator, snippet, 1.0)])
