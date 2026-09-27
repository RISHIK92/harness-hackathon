"""Loads the Meridian seed corpus into the running stack:

- ingests `data/repo/` through the normal repo-ingestion pipeline (Postgres
  Repo/Job rows, Neo4j module graph, `code_chunks` Qdrant collection) so
  `app.tools.code` works against it exactly like a real customer repo.
- embeds `data/docs/`, `data/tickets.jsonl`, `data/slack.jsonl` into three
  dedicated Qdrant collections (`kb_docs`, `kb_tickets`, `kb_slack`) for
  `app.tools.knowledge`.
- exposes plain JSON/JSONL readers for `data/accounts.json`,
  `data/commits.jsonl`, `data/prs.jsonl`, `data/logs.jsonl`,
  `data/incidents.jsonl` for `app.tools.tenant` / `app.tools.provenance`.

Idempotent: re-running skips ingestion if the repo already exists and
skips embedding a KB collection that's already populated.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from functools import lru_cache
from pathlib import Path

import structlog
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams
from sqlmodel import Session, create_engine, select

from app.config import get_settings
from app.core.embedding.embedder import VECTOR_SIZE, embed_texts, get_qdrant
from app.models import Job, JobStatus, Repo, RepoSourceType, RepoStatus
from app.tasks.ingestion import run_ingestion

log = structlog.get_logger()
settings = get_settings()

DATA_DIR = Path(__file__).resolve().parent / "data"
REPO_PATH = str(DATA_DIR / "repo")
SEED_REPO_NAME = "meridian-api"

KB_COLLECTIONS = {"docs": "kb_docs", "tickets": "kb_tickets", "slack": "kb_slack"}


# ─── plain JSON/JSONL readers (module-level cache; corpus is static at runtime) ─

def _read_jsonl(name: str) -> list[dict]:
    path = DATA_DIR / name
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


@lru_cache
def load_accounts() -> list[dict]:
    with open(DATA_DIR / "accounts.json") as f:
        return json.load(f)


@lru_cache
def load_commits() -> tuple[dict, ...]:
    return tuple(_read_jsonl("commits.jsonl"))


@lru_cache
def load_prs() -> tuple[dict, ...]:
    return tuple(_read_jsonl("prs.jsonl"))


@lru_cache
def load_tickets() -> tuple[dict, ...]:
    return tuple(_read_jsonl("tickets.jsonl"))


@lru_cache
def load_logs() -> tuple[dict, ...]:
    return tuple(_read_jsonl("logs.jsonl"))


@lru_cache
def load_incidents() -> tuple[dict, ...]:
    return tuple(_read_jsonl("incidents.jsonl"))


@lru_cache
def load_slack() -> tuple[dict, ...]:
    return tuple(_read_jsonl("slack.jsonl"))


def get_account(account_id: str) -> dict | None:
    for a in load_accounts():
        if a["id"] == account_id:
            return a
    return None


# ─── repo ingestion ──────────────────────────────────────────────────────────

_sync_engine = None


def _engine():
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(settings.sync_database_url)
    return _sync_engine


def ensure_repo_ingested(wait: bool = True, timeout_s: int = 180) -> str:
    """Return the repo_id for the Meridian seed repo, ingesting it via the
    normal pipeline (Postgres + Neo4j + Qdrant `code_chunks`) if needed."""
    with Session(_engine()) as session:
        existing = session.exec(select(Repo).where(Repo.name == SEED_REPO_NAME)).first()
        if existing:
            if existing.status == RepoStatus.READY:
                return existing.id
            if existing.status == RepoStatus.FAILED:
                raise RuntimeError(
                    f"seed repo {existing.id} previously FAILED ingestion: {existing.error_message}"
                )
            repo_id = existing.id
        else:
            repo = Repo(
                name=SEED_REPO_NAME,
                source_type=RepoSourceType.LOCAL,
                source_url=REPO_PATH,
                status=RepoStatus.PENDING,
                owner_id=None,
            )
            session.add(repo)
            session.commit()
            session.refresh(repo)
            repo_id = repo.id

            job = Job(repo_id=repo_id)
            session.add(job)
            session.commit()
            session.refresh(job)

            run_ingestion.apply_async(args=[repo_id, job.id], task_id=job.id)
            log.info("seed.repo_ingestion_dispatched", repo_id=repo_id)

    if not wait:
        return repo_id

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with Session(_engine()) as session:
            repo = session.get(Repo, repo_id)
            if repo.status == RepoStatus.READY:
                return repo_id
            if repo.status == RepoStatus.FAILED:
                raise RuntimeError(f"seed repo ingestion failed: {repo.error_message}")
        time.sleep(2)
    raise TimeoutError(f"seed repo ingestion did not finish READY within {timeout_s}s")


def get_seed_repo_id() -> str | None:
    """Non-blocking lookup — returns None if the seed repo hasn't been ingested yet."""
    with Session(_engine()) as session:
        repo = session.exec(select(Repo).where(Repo.name == SEED_REPO_NAME)).first()
        if repo and repo.status == RepoStatus.READY:
            return repo.id
        return None


# ─── knowledge-base embedding (docs / tickets / slack) ──────────────────────

def _ensure_kb_collection(name: str) -> None:
    client = get_qdrant()
    existing = {c.name for c in client.get_collections().collections}
    if name not in existing:
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def _collection_populated(name: str) -> bool:
    client = get_qdrant()
    try:
        info = client.get_collection(name)
        return info.points_count and info.points_count > 0
    except Exception:
        return False


def _upsert(collection: str, records: list[tuple[str, str, dict]]) -> None:
    """records: list of (stable_id, text, payload)"""
    if not records:
        return
    texts = [r[1] for r in records]
    vectors = embed_texts(texts, input_type="document")
    points = [
        PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, stable_id)),
            vector=vec,
            payload={**payload, "text": text},
        )
        for (stable_id, text, payload), vec in zip(records, vectors)
    ]
    get_qdrant().upsert(collection_name=collection, points=points)
    log.info("seed.kb_upserted", collection=collection, count=len(points))


def _title_from_markdown(text: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def embed_docs(force: bool = False) -> None:
    _ensure_kb_collection(KB_COLLECTIONS["docs"])
    if not force and _collection_populated(KB_COLLECTIONS["docs"]):
        return
    records = []
    for path in sorted((DATA_DIR / "docs").glob("*.md")):
        if path.name == "CONFLICTS.md":
            # Build-time reference only (documents the intentional S3 seed
            # contradiction for whoever's working on this repo) — must NEVER
            # be embedded as searchable product knowledge, or check_conflict
            # ends up comparing "docs" against a doc that already correctly
            # narrates the conflict, which spuriously reports "agrees".
            continue
        text = path.read_text()
        title = _title_from_markdown(text) or path.stem
        records.append(
            (
                f"doc:{path.name}",
                text,
                {
                    "doc_id": path.stem,
                    "title": title,
                    "path": f"docs/{path.name}",
                },
            )
        )
    _upsert(KB_COLLECTIONS["docs"], records)


def embed_tickets(force: bool = False) -> None:
    _ensure_kb_collection(KB_COLLECTIONS["tickets"])
    if not force and _collection_populated(KB_COLLECTIONS["tickets"]):
        return
    records = []
    for t in load_tickets():
        text = (
            f"{t['title']}\n\nStatus: {t['status']}\n"
            f"Account: {t.get('account_id') or 'internal'}\n"
            f"Resolution: {t.get('resolution') or '(unresolved)'}"
        )
        records.append(
            (
                f"ticket:{t['id']}",
                text,
                {
                    "ticket_id": t["id"],
                    "account_id": t.get("account_id"),
                    "status": t["status"],
                    "opened_at": t["opened_at"],
                    "resolved_at": t.get("resolved_at"),
                },
            )
        )
    _upsert(KB_COLLECTIONS["tickets"], records)


def embed_slack(force: bool = False) -> None:
    _ensure_kb_collection(KB_COLLECTIONS["slack"])
    if not force and _collection_populated(KB_COLLECTIONS["slack"]):
        return
    records = []
    for m in _read_jsonl("slack.jsonl"):
        text = f"[#{m['channel']}] {m['user_name']} ({m['user_role']}): {m['text']}"
        records.append(
            (
                f"slack:{m['channel']}:{m['ts']}",
                text,
                {
                    "channel": m["channel"],
                    "user_name": m["user_name"],
                    "user_role": m["user_role"],
                    "ts": m["ts"],
                    "thread_ts": m["thread_ts"],
                    "datetime": m["datetime"],
                },
            )
        )
    _upsert(KB_COLLECTIONS["slack"], records)


def embed_knowledge_base(force: bool = False) -> None:
    embed_docs(force=force)
    embed_tickets(force=force)
    embed_slack(force=force)


async def kb_search(kind: str, query: str, top_k: int = 6, query_filter: Filter | None = None) -> list[dict]:
    """Search one of the kb_* collections. Runs the blocking Qdrant call inline
    (loader/tool layer isn't on the hot SSE path the way vector_search is)."""
    import asyncio

    collection = KB_COLLECTIONS[kind]

    def _search():
        vec = embed_texts([query], input_type="query")[0]
        hits = get_qdrant().search(
            collection_name=collection,
            query_vector=vec,
            limit=top_k,
            query_filter=query_filter,
            with_payload=True,
        )
        return [{"payload": h.payload, "score": h.score} for h in hits if h.payload]

    return await asyncio.get_event_loop().run_in_executor(None, _search)


def load_all(wait_for_repo: bool = True) -> dict:
    """Convenience: ingest repo + embed KB. Call once at startup / from a
    one-off script; safe to call repeatedly (idempotent)."""
    repo_id = ensure_repo_ingested(wait=wait_for_repo)
    embed_knowledge_base()
    return {"repo_id": repo_id}
