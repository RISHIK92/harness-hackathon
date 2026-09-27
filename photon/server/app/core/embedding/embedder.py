from __future__ import annotations
import asyncio
import uuid

import structlog
import voyageai
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)

from app.config import get_settings
from app.core.embedding.chunker import Chunk

log = structlog.get_logger()
settings = get_settings()

COLLECTION = "code_chunks"
VECTOR_SIZE = 1024  # voyage-code-3 default output dimension

_qdrant: QdrantClient | None = None
_voyage: voyageai.Client | None = None


def get_qdrant() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        _ensure_collection(_qdrant)
    return _qdrant


def get_voyage() -> voyageai.Client:
    global _voyage
    if _voyage is None:
        _voyage = voyageai.Client(api_key=settings.voyage_api_key)
    return _voyage


def _ensure_collection(client: QdrantClient) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION not in existing:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def embed_texts(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """Call Voyage AI embedding API and return vectors."""
    client = get_voyage()
    result = client.embed(
        texts,
        model=settings.voyage_embedding_model,
        input_type=input_type,
    )
    return result.embeddings


def upsert_chunks(chunks: list[Chunk]) -> None:
    """Embed a batch of chunks and upsert into Qdrant."""
    if not chunks:
        return

    qdrant = get_qdrant()
    texts = [c.text for c in chunks]
    vectors = embed_texts(texts, input_type="document")

    points = [
        PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, c.chunk_id)),
            vector=vec,
            payload={
                "chunk_id": c.chunk_id,
                "repo_id": c.repo_id,
                "file_path": c.file_path,
                "language": c.language,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "symbol_name": c.symbol_name,
                "text": c.text,
                **c.metadata,
            },
        )
        for c, vec in zip(chunks, vectors)
    ]
    qdrant.upsert(collection_name=COLLECTION, points=points)
    log.info("embedder.upserted", count=len(points))


def _sync_vector_search(
    repo_id: str | None, question: str, top_k: int, workspace_id: str | None = None
) -> list[dict]:
    """Synchronous inner search — runs in a thread executor.

    repo_id=None + workspace_id set searches every repo in that workspace
    (chunks are tagged with workspace_id at ingest time — see
    app.tasks.ingestion) and lets relevance ranking sort out which repo's
    chunks actually answer the question. This is the multi-repo case: a
    workspace with several repos and no explicit repo named in the
    question. repo_id still wins when given — same single-repo behavior
    as before.
    """
    qdrant = get_qdrant()

    query_vec = embed_texts([question], input_type="query")[0]

    if repo_id:
        query_filter = Filter(must=[FieldCondition(key="repo_id", match=MatchValue(value=repo_id))])
    elif workspace_id:
        query_filter = Filter(must=[FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id))])
    else:
        query_filter = None

    hits = qdrant.search(
        collection_name=COLLECTION,
        query_vector=query_vec,
        limit=top_k,
        query_filter=query_filter,
        with_payload=True,
    )
    # Keep Qdrant's real cosine score on the payload. It used to be dropped
    # here, which forced every consumer to fake a score from rank order
    # (app.tools.code._rank_score) — so a 0.77 match and a 0.42 one looked
    # identical downstream, and a reranker had nothing to fuse with.
    results = []
    for hit in hits:
        if not hit.payload:
            continue
        payload = dict(hit.payload)
        payload["_score"] = float(hit.score)
        results.append(payload)
    log.info("embedder.search", repo_id=repo_id, workspace_id=workspace_id, hits=len(results))
    return results


async def vector_search(
    repo_id: str | None,
    question: str,
    top_k: int = 10,
    workspace_id: str | None = None,
) -> list[dict]:
    """Embed the query and search Qdrant. Runs blocking I/O in a thread."""
    return await asyncio.get_event_loop().run_in_executor(
        None, _sync_vector_search, repo_id, question, top_k, workspace_id
    )


def delete_repo_chunks(repo_id: str) -> None:
    """Remove all chunks for a repo from Qdrant."""
    qdrant = get_qdrant()
    qdrant.delete(
        collection_name=COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="repo_id", match=MatchValue(value=repo_id))]
        ),
    )


