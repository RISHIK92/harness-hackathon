"""Pull selected Slack channels into the vector store.

Runs in Celery (sync client, like ingestion). Messages land in ONE Qdrant
collection with `workspace_id` in the payload rather than a collection per
tenant: Qdrant filters on payload cheaply, and a collection per workspace
would multiply collections without bound as customers are added.

Reuses the seed corpus's embedding path, so real Slack and the fixture
corpus are searched identically — which is what makes the fixture a
faithful rehearsal for real data rather than a separate code path.
"""
from __future__ import annotations

import time
import uuid
from typing import Iterable, Optional

import httpx
import structlog
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

from app.core.embedding.embedder import VECTOR_SIZE, embed_texts, get_qdrant

log = structlog.get_logger()

COLLECTION = "slack_messages"
_EMBED_BATCH = 100
# Slack's own limit per history call; more pages cost more rate-limited
# round-trips, so this is the page size, not a total cap.
_PAGE = 200


def ensure_collection() -> None:
    client = get_qdrant()
    if COLLECTION not in {c.name for c in client.get_collections().collections}:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def _get(client: httpx.Client, token: str, method: str, **params) -> dict:
    """One Slack API call, honouring rate limits.

    Slack answers 429 with Retry-After and MEANS it — ignoring that gets an
    app throttled for everyone in the workspace, so this waits rather than
    hammering.
    """
    for _ in range(5):
        resp = client.get(
            f"https://slack.com/api/{method}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", "2"))
            log.info("slack_sync.rate_limited", method=method, wait_seconds=wait)
            time.sleep(wait)
            continue
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"slack {method} failed: {data.get('error')}")
        return data
    raise RuntimeError(f"slack {method} kept rate-limiting")


def _user_directory(client: httpx.Client, token: str) -> dict[str, str]:
    """user id -> display name, so a message reads "Priya Nair said…" rather
    than "U024BE7LH said…". Fetched once per sync, not per message."""
    names: dict[str, str] = {}
    cursor = ""
    while True:
        data = _get(client, token, "users.list", limit=200, **({"cursor": cursor} if cursor else {}))
        for member in data.get("members", []):
            profile = member.get("profile") or {}
            names[member["id"]] = (
                profile.get("display_name") or profile.get("real_name") or member.get("name") or member["id"]
            )
        cursor = (data.get("response_metadata") or {}).get("next_cursor", "")
        if not cursor:
            return names


def _channel_messages(
    client: httpx.Client, token: str, channel_id: str, oldest: Optional[float] = None
) -> Iterable[dict]:
    """Top-level messages plus thread replies.

    Threads matter more than the channel timeline here: the answer to "why
    does pricing special-case Bangalore" lives in a reply, not in the parent
    message that started the thread.
    """
    cursor = ""
    while True:
        data = _get(
            client, token, "conversations.history",
            channel=channel_id, limit=_PAGE,
            **({"cursor": cursor} if cursor else {}),
            **({"oldest": str(oldest)} if oldest else {}),
        )
        for message in data.get("messages", []):
            if message.get("subtype") in {"channel_join", "channel_leave"}:
                continue  # noise; never the answer to anything
            yield message
            if message.get("thread_ts") and message.get("reply_count"):
                rcursor = ""
                while True:
                    replies = _get(
                        client, token, "conversations.replies",
                        channel=channel_id, ts=message["thread_ts"], limit=_PAGE,
                        **({"cursor": rcursor} if rcursor else {}),
                    )
                    for reply in replies.get("messages", []):
                        if reply.get("ts") != message.get("ts"):
                            yield reply
                    rcursor = (replies.get("response_metadata") or {}).get("next_cursor", "")
                    if not rcursor:
                        break
        cursor = (data.get("response_metadata") or {}).get("next_cursor", "")
        if not cursor:
            return


def sync_channel(
    token: str, workspace_id: str, channel_id: str, channel_name: str, oldest: Optional[float] = None
) -> int:
    """Fetch, embed and upsert one channel. Returns the message count."""
    ensure_collection()
    with httpx.Client(timeout=30.0) as client:
        names = _user_directory(client, token)
        messages = [m for m in _channel_messages(client, token, channel_id, oldest) if (m.get("text") or "").strip()]

    if not messages:
        return 0

    return index_messages(workspace_id, channel_id, channel_name, messages, names)


def index_messages(
    workspace_id: str,
    channel_id: str,
    channel_name: str,
    messages: list[dict],
    names: Optional[dict[str, str]] = None,
) -> int:
    """Embed and upsert messages. Shared by the live OAuth sync and the
    export importer so both produce IDENTICAL points — same locator shape,
    same deterministic ids, same payload. A separate path for imported data
    would mean the demo rehearses something the product does not do.
    """
    ensure_collection()
    names = names or {}
    if not messages:
        return 0

    texts, payloads = [], []
    for m in messages:
        author = names.get(m.get("user", ""), m.get("username") or "unknown")
        # The author and channel are embedded with the text on purpose:
        # "what did Priya say about pricing" should match on the name too,
        # not only on the words of the message.
        texts.append(f"#{channel_name} — {author}: {m['text']}")
        payloads.append({
            "workspace_id": workspace_id,
            "channel_id": channel_id,
            "channel": channel_name,
            "user": author,
            "ts": m.get("ts"),
            "thread_ts": m.get("thread_ts"),
            "text": m["text"],
        })

    client_q = get_qdrant()
    for start in range(0, len(texts), _EMBED_BATCH):
        chunk_texts = texts[start : start + _EMBED_BATCH]
        vectors = embed_texts(chunk_texts)
        client_q.upsert(
            collection_name=COLLECTION,
            points=[
                PointStruct(
                    # Deterministic id from workspace+channel+ts, so a
                    # re-sync UPDATES a message rather than duplicating it.
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{workspace_id}:{channel_id}:{p['ts']}")),
                    vector=v,
                    payload=p,
                )
                for v, p in zip(vectors, payloads[start : start + _EMBED_BATCH])
            ],
        )
    log.info("slack_sync.channel_done", channel=channel_name, messages=len(messages))
    return len(messages)


def search(workspace_id: str, query: str, channel: Optional[str] = None, limit: int = 8) -> list[dict]:
    """Vector search over one workspace's Slack history."""
    ensure_collection()
    must = [FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id))]
    if channel:
        must.append(FieldCondition(key="channel", match=MatchValue(value=channel.lstrip("#"))))
    vector = embed_texts([query], input_type="query")[0]
    hits = get_qdrant().search(
        collection_name=COLLECTION,
        query_vector=vector,
        query_filter=Filter(must=must),
        limit=limit,
    )
    return [{**h.payload, "score": h.score} for h in hits]


def has_data(workspace_id: str) -> bool:
    """Whether this workspace has any real Slack indexed — decides between
    real data and the demo fixture."""
    ensure_collection()
    hits = get_qdrant().scroll(
        collection_name=COLLECTION,
        scroll_filter=Filter(must=[FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id))]),
        limit=1,
    )
    return bool(hits and hits[0])
