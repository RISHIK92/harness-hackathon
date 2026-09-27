"""The connector contract, and the shared vector store behind it.

One Qdrant collection for every provider, filtered by `workspace_id` AND
`provider` inside the query. Not a collection per vendor: collections are a
schema-level thing in Qdrant, and adding one per integration per tenant
multiplies without bound for no retrieval benefit — a payload filter does
the same job.
"""
from __future__ import annotations

import uuid
from typing import Protocol

import structlog
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

from app.core.embedding.embedder import VECTOR_SIZE, embed_texts, get_qdrant

log = structlog.get_logger()

COLLECTION = "connector_items"
_EMBED_BATCH = 100


class Item(dict):
    """One indexable thing: {external_id, title, text, url, meta}."""


class Connector(Protocol):
    """What every provider must supply. Deliberately small — anything a
    vendor needs beyond this belongs in its own module, not in the shared
    contract."""

    provider: str

    def verify(self, credentials: dict, config: dict) -> dict:
        """Prove the credentials work; return display info. Raises ValueError
        with a human-readable reason when they don't."""

    def list_resources(self, credentials: dict, config: dict) -> list[dict]:
        """Selectable units: [{id, name}]."""

    def fetch(self, credentials: dict, config: dict, resource_id: str) -> list[Item]:
        """Items to index for one resource."""


def ensure_collection() -> None:
    client = get_qdrant()
    if COLLECTION not in {c.name for c in client.get_collections().collections}:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def index_items(workspace_id: str, provider: str, resource_id: str, items: list[Item]) -> int:
    ensure_collection()
    items = [i for i in items if (i.get("text") or "").strip()]
    if not items:
        return 0

    client = get_qdrant()
    for start in range(0, len(items), _EMBED_BATCH):
        batch = items[start : start + _EMBED_BATCH]
        vectors = embed_texts([f"{i.get('title','')}\n{i['text']}"[:4000] for i in batch])
        client.upsert(
            collection_name=COLLECTION,
            points=[
                PointStruct(
                    # Keyed on provider + external id so a re-sync replaces
                    # an item rather than leaving a stale copy the agent
                    # could still cite as current.
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{workspace_id}:{provider}:{i['external_id']}")),
                    vector=v,
                    payload={
                        "workspace_id": workspace_id,
                        "provider": provider,
                        "resource_id": resource_id,
                        "external_id": i["external_id"],
                        "title": i.get("title", ""),
                        "url": i.get("url", ""),
                        "text": i["text"][:4000],
                        **(i.get("meta") or {}),
                    },
                )
                for v, i in zip(vectors, batch)
            ],
        )
    log.info("connector.indexed", provider=provider, resource=resource_id, items=len(items))
    return len(items)


def search(workspace_id: str, provider: str, query: str, limit: int = 8) -> list[dict]:
    ensure_collection()
    vector = embed_texts([query], input_type="query")[0]
    hits = get_qdrant().search(
        collection_name=COLLECTION,
        query_vector=vector,
        query_filter=Filter(
            must=[
                FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id)),
                FieldCondition(key="provider", match=MatchValue(value=provider)),
            ]
        ),
        limit=limit,
    )
    return [{**h.payload, "score": h.score} for h in hits]


def has_data(workspace_id: str, provider: str) -> bool:
    ensure_collection()
    found = get_qdrant().scroll(
        collection_name=COLLECTION,
        scroll_filter=Filter(
            must=[
                FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id)),
                FieldCondition(key="provider", match=MatchValue(value=provider)),
            ]
        ),
        limit=1,
    )
    return bool(found and found[0])
