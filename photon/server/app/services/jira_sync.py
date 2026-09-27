"""Pull Jira issues into the vector store.

Same shape as slack_sync: one collection, filtered by `workspace_id` inside
the query. Auth is Basic (account email + API token) against the site's own
REST API, so it works from localhost with no registered app.

What gets indexed is deliberately narrow: summary, description, status,
assignee, and comments. Not every field — a Jira issue carries a great deal
of workflow metadata that no one will ever ask a support question about,
and embedding it dilutes the text that matters.
"""
from __future__ import annotations

import base64
import time
import uuid
from typing import Iterable, Optional

import httpx
import structlog
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

from app.core.embedding.embedder import VECTOR_SIZE, embed_texts, get_qdrant

log = structlog.get_logger()

COLLECTION = "jira_issues"
_EMBED_BATCH = 100
_PAGE = 100


def ensure_collection() -> None:
    client = get_qdrant()
    if COLLECTION not in {c.name for c in client.get_collections().collections}:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def auth_header(email: str, token: str) -> dict:
    basic = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {"Authorization": f"Basic {basic}", "Accept": "application/json"}


def verify(site_url: str, email: str, token: str) -> dict:
    """Check credentials before storing them.

    Storing an unverified token means the first failure surfaces later,
    during a background sync, where nobody is watching.
    """
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(f"{site_url.rstrip('/')}/rest/api/3/myself", headers=auth_header(email, token))
    if resp.status_code == 401:
        raise ValueError("Jira rejected those credentials (check the email and API token)")
    if resp.status_code == 404:
        raise ValueError("That site URL doesn't look like a Jira site")
    resp.raise_for_status()
    me = resp.json()
    return {"account_id": me.get("accountId"), "display_name": me.get("displayName"), "email": me.get("emailAddress")}


def list_projects(site_url: str, email: str, token: str) -> list[dict]:
    projects: list[dict] = []
    start = 0
    with httpx.Client(timeout=20.0) as client:
        while True:
            resp = client.get(
                f"{site_url.rstrip('/')}/rest/api/3/project/search",
                headers=auth_header(email, token),
                params={"startAt": start, "maxResults": _PAGE},
            )
            resp.raise_for_status()
            data = resp.json()
            projects.extend(
                {"key": p["key"], "name": p.get("name", p["key"]), "id": p.get("id")}
                for p in data.get("values", [])
            )
            if data.get("isLast", True):
                return projects
            start += _PAGE


def _plain_text(adf) -> str:
    """Jira Cloud returns rich text as Atlassian Document Format, a nested
    node tree. Only the text nodes are useful for retrieval."""
    if adf is None:
        return ""
    if isinstance(adf, str):
        return adf
    out: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text" and node.get("text"):
                out.append(node["text"])
            for child in node.get("content", []) or []:
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(adf)
    return " ".join(out)


def _issues(client: httpx.Client, site_url: str, headers: dict, jql: str) -> Iterable[dict]:
    start = 0
    while True:
        resp = client.get(
            f"{site_url.rstrip('/')}/rest/api/3/search",
            headers=headers,
            params={
                "jql": jql,
                "startAt": start,
                "maxResults": _PAGE,
                # Ask for only what is indexed; Jira returns a lot otherwise.
                "fields": "summary,description,status,assignee,reporter,priority,labels,updated,comment",
            },
        )
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", "2"))
            time.sleep(wait)
            continue
        resp.raise_for_status()
        data = resp.json()
        issues = data.get("issues", [])
        for issue in issues:
            yield issue
        start += len(issues)
        if start >= data.get("total", 0) or not issues:
            return


def sync_project(
    site_url: str, email: str, token: str, workspace_id: str, project_key: str,
    updated_since: Optional[str] = None,
) -> int:
    headers = auth_header(email, token)
    jql = f'project = "{project_key}"'
    if updated_since:
        # Incremental: only what changed. Re-embedding an unchanged backlog
        # costs money and buys nothing.
        jql += f' AND updated >= "{updated_since}"'
    jql += " ORDER BY updated DESC"

    issues_payload = []
    with httpx.Client(timeout=40.0) as client:
        for issue in _issues(client, site_url, headers, jql):
            fields = issue.get("fields") or {}
            summary = fields.get("summary") or ""
            description = _plain_text(fields.get("description"))
            comments = " ".join(
                _plain_text(c.get("body")) for c in ((fields.get("comment") or {}).get("comments") or [])
            )
            status = ((fields.get("status") or {}).get("name")) or "unknown"
            assignee = ((fields.get("assignee") or {}) or {}).get("displayName") or "unassigned"

            body = f"{issue['key']} [{status}] {summary}\n{description}\n{comments}".strip()
            if not body:
                continue
            issues_payload.append({
                "issue_key": issue["key"],
                "summary": summary,
                "status": status,
                "assignee": assignee,
                "url": f"{site_url.rstrip('/')}/browse/{issue['key']}",
                "updated": fields.get("updated"),
                "text": body[:4000],
            })

    return index_issues(workspace_id, project_key, issues_payload)


def index_issues(workspace_id: str, project_key: str, issues: list[dict]) -> int:
    """Embed and upsert already-fetched issues. Shared by the live JQL sync
    above and app/mock/loader.py's fabricated issues, so both produce
    IDENTICAL points — same locator shape, same deterministic id, same
    payload — and search() cannot tell them apart at query time. Each
    dict needs: issue_key, summary, status, assignee, url, updated, text.
    """
    ensure_collection()
    issues = [i for i in issues if (i.get("text") or "").strip()]
    if not issues:
        return 0

    texts = [i["text"][:4000] for i in issues]
    payloads = [{"workspace_id": workspace_id, "project_key": project_key, **i, "text": i["text"][:4000]} for i in issues]

    client_q = get_qdrant()
    for start in range(0, len(texts), _EMBED_BATCH):
        vectors = embed_texts(texts[start : start + _EMBED_BATCH])
        client_q.upsert(
            collection_name=COLLECTION,
            points=[
                PointStruct(
                    # Keyed on the issue, so a re-sync of an updated ticket
                    # replaces it instead of leaving a stale copy that the
                    # agent might still cite.
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{workspace_id}:{p['issue_key']}")),
                    vector=v,
                    payload=p,
                )
                for v, p in zip(vectors, payloads[start : start + _EMBED_BATCH])
            ],
        )
    log.info("jira_sync.project_done", project=project_key, issues=len(texts))
    return len(texts)


def search(workspace_id: str, query: str, project_key: Optional[str] = None, limit: int = 8) -> list[dict]:
    ensure_collection()
    must = [FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id))]
    if project_key:
        must.append(FieldCondition(key="project_key", match=MatchValue(value=project_key)))
    vector = embed_texts([query], input_type="query")[0]
    hits = get_qdrant().search(
        collection_name=COLLECTION, query_vector=vector, query_filter=Filter(must=must), limit=limit
    )
    return [{**h.payload, "score": h.score} for h in hits]


def has_data(workspace_id: str) -> bool:
    ensure_collection()
    found = get_qdrant().scroll(
        collection_name=COLLECTION,
        scroll_filter=Filter(must=[FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id))]),
        limit=1,
    )
    return bool(found and found[0])
