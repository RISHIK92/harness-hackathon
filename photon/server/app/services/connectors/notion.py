"""Notion — internal integration token.

Notion's permission model is the important detail: an integration sees
NOTHING until a page is explicitly shared with it. So "connected but no
results" is the normal first state, and the UI has to say so — otherwise it
reads as a broken integration when it is actually working exactly as Notion
intends.
"""
from __future__ import annotations

import httpx

from app.services.connectors.base import Item

provider = "notion"
_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"  # Notion requires an explicit API version header


def _headers(credentials: dict) -> dict:
    return {
        "Authorization": f"Bearer {credentials['token']}",
        "Notion-Version": _VERSION,
        "Content-Type": "application/json",
    }


def verify(credentials: dict, config: dict) -> dict:
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(f"{_API}/users/me", headers=_headers(credentials))
    if resp.status_code == 401:
        raise ValueError("Notion rejected that integration token")
    resp.raise_for_status()
    me = resp.json()
    bot = (me.get("bot") or {}).get("workspace_name")
    return {"display_name": bot or me.get("name") or "Notion", "account": me.get("id")}


def _search(credentials: dict, filter_type: str) -> list[dict]:
    results, cursor = [], None
    with httpx.Client(timeout=30.0) as client:
        while True:
            body = {"filter": {"value": filter_type, "property": "object"}, "page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            resp = client.post(f"{_API}/search", headers=_headers(credentials), json=body)
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                return results
            cursor = data.get("next_cursor")


def _title_of(obj: dict) -> str:
    props = obj.get("properties") or {}
    for value in props.values():
        if value.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in value.get("title", []))
    title = obj.get("title")
    if isinstance(title, list):
        return "".join(t.get("plain_text", "") for t in title)
    return "Untitled"


def list_resources(credentials: dict, config: dict) -> list[dict]:
    """Top-level pages shared with the integration."""
    pages = _search(credentials, "page")
    return [{"id": p["id"], "name": _title_of(p) or p["id"]} for p in pages]


def _blocks_text(credentials: dict, block_id: str, depth: int = 0) -> str:
    """Flatten a page's blocks into text.

    Depth-limited: Notion pages nest arbitrarily and a runaway recursion on
    a deeply nested wiki would issue thousands of API calls for text nobody
    reads.
    """
    if depth > 2:
        return ""
    out: list[str] = []
    cursor = None
    with httpx.Client(timeout=30.0) as client:
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            resp = client.get(
                f"{_API}/blocks/{block_id}/children", headers=_headers(credentials), params=params
            )
            if resp.status_code == 404:
                return ""
            resp.raise_for_status()
            data = resp.json()
            for block in data.get("results", []):
                btype = block.get("type", "")
                payload = block.get(btype) or {}
                for text in payload.get("rich_text", []) or []:
                    out.append(text.get("plain_text", ""))
                if block.get("has_children"):
                    out.append(_blocks_text(credentials, block["id"], depth + 1))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
    return " ".join(t for t in out if t)


def fetch(credentials: dict, config: dict, resource_id: str) -> list[Item]:
    text = _blocks_text(credentials, resource_id)
    if not text.strip():
        return []
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(f"{_API}/pages/{resource_id}", headers=_headers(credentials))
        page = resp.json() if resp.status_code == 200 else {}
    return [Item(
        external_id=resource_id,
        title=_title_of(page),
        text=text,
        url=page.get("url", ""),
        meta={},
    )]
