"""Linear as a ticket source for agent jobs — intake, plan comment, approval.

Uses the Linear connection a member (or the workspace) already made for
search — the same API key, at the same access level. So the agent can only
pick up, read and comment on issues the person who connected Linear can,
which is the delegated-access rule applied to tickets.

A personal API key comments AS its owner; there is no separate bot identity
without a Linear OAuth app. Every comment is therefore signed "Photon, agent
for <member>" in its first line, so nobody reads the agent's plan as the
member typing it.

The webhook is verified with Linear's HMAC (`Linear-Signature`, hex SHA-256
of the raw body) and then re-reads the issue through the API rather than
trusting the payload: a webhook tells us WHAT changed, the API tells us what
it IS.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Optional

from app.services.connectors.linear import _query


def signature_ok(secret: str, body: bytes, header: Optional[str]) -> bool:
    if not secret or not header:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def fetch_issue(credentials: dict, issue_id: str) -> Optional[dict]:
    try:
        data = _query(credentials, """
            query($id: String!) { issue(id: $id) {
              id identifier title description url
              labels { nodes { name } }
              assignee { email }
              team { id key }
            } }""", {"id": issue_id})
    except Exception:  # noqa: BLE001 — not visible to this key is a normal answer
        return None
    return data.get("issue")


def fetch_comment(credentials: dict, comment_id: str) -> Optional[dict]:
    try:
        data = _query(credentials, """
            query($id: String!) { comment(id: $id) {
              id body user { email } issue { id identifier }
            } }""", {"id": comment_id})
    except Exception:  # noqa: BLE001
        return None
    return data.get("comment")


def post_comment(credentials: dict, issue_id: str, body: str) -> None:
    _query(credentials, """
        mutation($input: CommentCreateInput!) { commentCreate(input: $input) { success } }""",
        {"input": {"issueId": issue_id, "body": body}})


def labels_of(issue: dict) -> list[str]:
    return [l.get("name", "") for l in (issue.get("labels") or {}).get("nodes", [])]


def signed(owner_name: str, body: str) -> str:
    return f"_Photon — agent for {owner_name}_\n\n{body}"
