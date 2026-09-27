from __future__ import annotations
import structlog

from app.config import get_settings

log = structlog.get_logger()
settings = get_settings()

# Maximum characters of code to include in the LLM prompt
_MAX_CONTEXT_CHARS = 12_000


def _format_chunk(chunk: dict, index: int) -> str:
    path = chunk.get("file_path", chunk.get("path", "unknown"))
    sl = chunk.get("start_line", "?")
    el = chunk.get("end_line", "?")
    lang = chunk.get("language", "")
    sym = chunk.get("symbol_name", "")
    header = f"[{index}] {path}:{sl}-{el}"
    if sym:
        header += f" ({sym})"
    return f"{header}\n```{lang}\n{chunk.get('text', '')}\n```"


async def assemble_context(
    chunks: list[dict],
    graph_nodes: list[dict],
    question: str,
) -> dict:
    """
    Build a prompt string and collect citation references.

    Returns a dict with:
        prompt       – full prompt string for the LLM
        cited_chunks – serialisable list of chunk references
        graph_nodes  – deduplicated graph node list
    """
    log.info("context_assembler", chunks=len(chunks), graph_nodes=len(graph_nodes))

    # ── Build cited_chunks list ───────────────────────────────────────────────
    cited_chunks = [
        {
            "index": i + 1,
            "file_path": c.get("file_path", c.get("path", "")),
            "start_line": c.get("start_line"),
            "end_line": c.get("end_line"),
            "symbol_name": c.get("symbol_name", ""),
            "language": c.get("language", ""),
            "chunk_id": c.get("chunk_id", ""),
            "text": c.get("text", ""),
        }
        for i, c in enumerate(chunks)
    ]

    # ── Assemble code context (truncate to budget) ────────────────────────────
    sections: list[str] = []
    total_chars = 0
    for i, chunk in enumerate(chunks):
        block = _format_chunk(chunk, i + 1)
        if total_chars + len(block) > _MAX_CONTEXT_CHARS:
            break
        sections.append(block)
        total_chars += len(block)

    # ── Graph context summary ─────────────────────────────────────────────────
    graph_summary = ""
    if graph_nodes:
        paths = [n.get("path", n.get("id", "")) for n in graph_nodes[:20]]
        graph_summary = (
            "\n\nRelated modules from dependency graph:\n"
            + "\n".join(f"  - {p}" for p in paths)
        )

    # ── Final prompt ──────────────────────────────────────────────────────────
    if sections:
        code_context = "\n\n".join(sections)
        prompt = (
            "You are an expert software engineer helping a developer understand a codebase.\n"
            "Use the provided code context to answer the question. "
            "Cite relevant file names and line numbers where appropriate.\n\n"
            f"### Question\n{question}\n\n"
            f"### Code Context\n{code_context}"
            f"{graph_summary}\n\n"
            "### Answer"
        )
    else:
        # No chunks retrieved — answer from general knowledge but be honest
        prompt = (
            "You are an expert software engineer helping a developer understand a codebase.\n"
            "No specific code snippets were retrieved for this question. "
            "Answer as helpfully as you can based on the question alone, "
            "and note if you would need to see specific files to give a more precise answer.\n\n"
            f"### Question\n{question}\n\n"
            "### Answer"
        )

    return {
        "prompt": prompt,
        "cited_chunks": cited_chunks,
        "graph_nodes": graph_nodes,
    }
