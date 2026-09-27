"""Linear — personal API key, GraphQL.

No OAuth: a Linear API key is created from personal settings in seconds and
carries that user's permissions. Same reasoning as Jira.
"""
from __future__ import annotations

import httpx

from app.services.connectors.base import Item

provider = "linear"
_API = "https://api.linear.app/graphql"


def _query(credentials: dict, query: str, variables: dict | None = None) -> dict:
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            _API,
            headers={"Authorization": credentials["api_key"], "Content-Type": "application/json"},
            json={"query": query, "variables": variables or {}},
        )
    if resp.status_code == 401:
        raise ValueError("Linear rejected that API key")
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise ValueError(f"Linear error: {data['errors'][0].get('message')}")
    return data["data"]


def verify(credentials: dict, config: dict) -> dict:
    me = _query(credentials, "{ viewer { id name email } organization { name } }")
    return {
        "display_name": f"{me['organization']['name']} ({me['viewer']['name']})",
        "account": me["viewer"].get("email"),
    }


def list_resources(credentials: dict, config: dict) -> list[dict]:
    data = _query(credentials, "{ teams(first: 100) { nodes { id key name } } }")
    return [{"id": t["id"], "name": f"{t['key']} — {t['name']}"} for t in data["teams"]["nodes"]]


def fetch(credentials: dict, config: dict, resource_id: str) -> list[Item]:
    """Issues for one team, with comments.

    Comments matter as much as the description here: Linear discussions
    carry the "we decided not to fix this because…" that a support answer
    needs, and it lives nowhere else.
    """
    items: list[Item] = []
    cursor = None
    while True:
        data = _query(
            credentials,
            """
            query($teamId: String!, $after: String) {
              team(id: $teamId) {
                issues(first: 50, after: $after) {
                  pageInfo { hasNextPage endCursor }
                  nodes {
                    identifier title description url
                    state { name }
                    assignee { name }
                    comments(first: 20) { nodes { body user { name } } }
                  }
                }
              }
            }
            """,
            {"teamId": resource_id, "after": cursor},
        )
        issues = data["team"]["issues"]
        for issue in issues["nodes"]:
            comments = " ".join(
                f"{(c.get('user') or {}).get('name', 'someone')}: {c.get('body', '')}"
                for c in (issue.get("comments") or {}).get("nodes", [])
            )
            state = (issue.get("state") or {}).get("name", "unknown")
            items.append(Item(
                external_id=issue["identifier"],
                title=f"{issue['identifier']} [{state}] {issue.get('title', '')}",
                text=f"{issue.get('description') or ''}\n{comments}".strip() or issue.get("title", ""),
                url=issue.get("url", ""),
                meta={"status": state, "assignee": (issue.get("assignee") or {}).get("name", "unassigned")},
            ))
        if not issues["pageInfo"]["hasNextPage"]:
            return items
        cursor = issues["pageInfo"]["endCursor"]
