"""Uploaded context documents.

Business flows, operations notes, escalation policies — the things that
live in a Google Doc or someone's head rather than in a repo or a ticket.
The agent answers "what's our process when a customer hits this" from here.

Indexed through the connector store (`provider="custom_docs"`), so there is
one retrieval path rather than a special case: same filter, same isolation,
same tool shape.
"""
from __future__ import annotations

import re

import structlog

from app.services.connectors.base import Item, index_items

log = structlog.get_logger()

# Chunk on markdown headings first, because an uploaded process doc is
# usually structured by them and a heading is the best summary of what
# follows. Long sections fall back to a size split.
_HEADING = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)
_MAX_CHARS = 1800
_OVERLAP = 150


def chunk_markdown(text: str, title: str) -> list[tuple[str, str]]:
    """Returns [(section_title, chunk_text)]."""
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []

    positions = [m.start() for m in _HEADING.finditer(text)]
    sections: list[tuple[str, str]] = []
    if not positions:
        sections = [(title, text)]
    else:
        if positions[0] > 0:
            sections.append((title, text[: positions[0]].strip()))
        for i, start in enumerate(positions):
            end = positions[i + 1] if i + 1 < len(positions) else len(text)
            block = text[start:end].strip()
            heading = block.splitlines()[0].lstrip("#").strip() or title
            sections.append((heading, block))

    out: list[tuple[str, str]] = []
    for heading, block in sections:
        if not block.strip():
            continue
        if len(block) <= _MAX_CHARS:
            out.append((heading, block))
            continue
        # Overlap so a sentence split across the boundary is still
        # retrievable from at least one chunk.
        start = 0
        while start < len(block):
            out.append((heading, block[start : start + _MAX_CHARS]))
            start += _MAX_CHARS - _OVERLAP
    return out


def index_document(workspace_id: str, doc_id: str, title: str, text: str) -> int:
    chunks = chunk_markdown(text, title)
    items = [
        Item(
            external_id=f"{doc_id}#{i}",
            title=f"{title} — {heading}" if heading != title else title,
            text=chunk,
            url="",
            meta={"doc_id": doc_id, "section": heading},
        )
        for i, (heading, chunk) in enumerate(chunks)
    ]
    count = index_items(workspace_id, "custom_docs", doc_id, items)
    log.info("custom_docs.indexed", doc_id=doc_id, chunks=count)
    return count
