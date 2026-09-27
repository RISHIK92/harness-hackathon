from __future__ import annotations

from app.models import QueryIntent
from app.core.embedding.embedder import vector_search
from app.core.graph.builder import Neo4jClient
from app.config import get_settings

settings = get_settings()


async def hybrid_retrieve(
    repo_id: str,
    question: str,
    intent: QueryIntent,
) -> tuple[list[dict], list[dict]]:
    """
    Hybrid retrieval combining vector search and graph traversal.

    Returns:
        chunks     – list of Qdrant payload dicts (code chunks)
        graph_nodes – list of Neo4j module/symbol dicts
    """
    top_k = settings.top_k_vector

    # ── Vector retrieval (all intents) ────────────────────────────────────────
    chunks = await vector_search(repo_id, question, top_k=top_k)

    # ── Graph retrieval (relational / cross-cutting / structural) ────────────
    graph_nodes: list[dict] = []

    if intent in (QueryIntent.RELATIONAL, QueryIntent.CROSS_CUTTING, QueryIntent.STRUCTURAL):
        client = Neo4jClient()
        try:
            # Use top vector hits as seeds for graph neighbourhood expansion
            seen_ids: set[str] = set()
            for chunk in chunks[:5]:
                file_path = chunk.get("file_path", "")
                if not file_path:
                    continue
                # Find the module node matching this file path
                modules = await client.search_modules_by_path(repo_id, file_path)
                for mod in modules:
                    node_id = mod["id"]
                    if node_id in seen_ids:
                        continue
                    seen_ids.add(node_id)
                    # Expand neighbourhood
                    neighbors = await client.get_neighbors(
                        node_id, repo_id, hops=settings.top_k_graph_hops
                    )
                    for n in neighbors:
                        if n["id"] not in seen_ids:
                            seen_ids.add(n["id"])
                            graph_nodes.append(n)
        finally:
            await client.close()

    return chunks, graph_nodes
