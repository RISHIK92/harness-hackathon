"""Docs / tickets / slack search — reads from the kb_docs / kb_tickets /
kb_slack Qdrant collections populated by app.seed.loader.embed_knowledge_base.
Kept as separate collections deliberately (see the build plan, Phase 2) so
the evidence panel can distinguish source types without relying on a payload
field alone.
"""
from __future__ import annotations

import asyncio

import structlog
from qdrant_client.models import FieldCondition, Filter, MatchValue

from app.seed.loader import kb_search
from app.tools.evidence import make_evidence, tool_error, tool_result

log = structlog.get_logger()


def _doc_to_evidence(hit: dict) -> dict:
    p = hit["payload"]
    return make_evidence("docs", p.get("path", p.get("doc_id", "?")), p.get("text", ""), hit["score"])


def _ticket_to_evidence(hit: dict) -> dict:
    p = hit["payload"]
    return make_evidence("ticket", f"ticket:{p.get('ticket_id', '?')}", p.get("text", ""), hit["score"])


def _slack_to_evidence(hit: dict) -> dict:
    p = hit["payload"]
    locator = f"slack:#{p.get('channel', '?')}:{p.get('ts', '?')}"
    return make_evidence("slack", locator, p.get("text", ""), hit["score"])


async def search_docs(query: str, top_k: int = 6) -> dict:
    try:
        hits = await kb_search("docs", query, top_k=top_k)
    except Exception as exc:  # noqa: BLE001
        log.error("tool.search_docs_error", error=str(exc))
        return tool_error("search_docs", f"search_docs failed: {exc}")
    evidence = [_doc_to_evidence(h) for h in hits]
    return tool_result("search_docs", evidence, note=None if evidence else f"no docs matched '{query}'")


async def search_tickets(query: str, account_id: str | None = None, top_k: int = 6) -> dict:
    query_filter = None
    if account_id:
        query_filter = Filter(must=[FieldCondition(key="account_id", match=MatchValue(value=account_id))])
    try:
        hits = await kb_search("tickets", query, top_k=top_k, query_filter=query_filter)
    except Exception as exc:  # noqa: BLE001
        log.error("tool.search_tickets_error", error=str(exc))
        return tool_error("search_tickets", f"search_tickets failed: {exc}")
    evidence = [_ticket_to_evidence(h) for h in hits]
    return tool_result("search_tickets", evidence, note=None if evidence else f"no tickets matched '{query}'")


async def search_slack(
    query: str, channel: str | None = None, top_k: int = 8, workspace_id: str | None = None
) -> dict:
    """Search Slack history.

    Prefers the workspace's REAL connected Slack when it has any indexed,
    and falls back to the seed corpus otherwise. Two reasons for the
    fallback rather than an empty result: the demo scenarios must keep
    working on a deployment with no Slack connected, and a workspace that
    has just connected Slack but not finished its first sync should degrade
    to "nothing found" behaviour rather than an error.

    The workspace filter is applied inside the vector query (Qdrant payload
    filter), never after: filtering post-hoc would let one tenant's messages
    occupy the top-k and silently starve another's results even though they
    are never shown.
    """
    if workspace_id:
        try:
            from app.services import slack_sync

            if await asyncio.get_event_loop().run_in_executor(None, slack_sync.has_data, workspace_id):
                hits = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: slack_sync.search(workspace_id, query, channel, top_k)
                )
                evidence = [_real_slack_to_evidence(h) for h in hits]
                return tool_result(
                    "search_slack",
                    evidence,
                    note=None if evidence else f"no Slack messages matched '{query}'",
                )
        except Exception as exc:  # noqa: BLE001 - fall back rather than fail the turn
            log.error("tool.search_slack_workspace_error", error=str(exc))

    query_filter = None
    if channel:
        query_filter = Filter(must=[FieldCondition(key="channel", match=MatchValue(value=channel))])
    try:
        hits = await kb_search("slack", query, top_k=top_k, query_filter=query_filter)
    except Exception as exc:  # noqa: BLE001
        log.error("tool.search_slack_error", error=str(exc))
        return tool_error("search_slack", f"search_slack failed: {exc}")
    evidence = [_slack_to_evidence(h) for h in hits]
    return tool_result("search_slack", evidence, note=None if evidence else f"no Slack messages matched '{query}'")


def _real_slack_to_evidence(hit: dict) -> dict:
    """Locator mirrors the fixture's shape (slack:#channel:ts) so the
    evidence panel, the citation rules and the verifier treat real and
    seeded Slack identically."""
    channel = hit.get("channel", "unknown")
    ts = hit.get("ts", "")
    user = hit.get("user", "unknown")
    return make_evidence(
        "slack",
        f"slack:#{channel}:{ts}",
        f"{user}: {hit.get('text', '')}",
        float(hit.get("score") or 0.0),
    )


async def search_jira(
    query: str, project_key: str | None = None, top_k: int = 8, workspace_id: str | None = None
) -> dict:
    """Search connected Jira issues.

    No seed-corpus fallback, unlike search_slack: the demo corpus has
    tickets.jsonl behind `search_tickets`, so a fallback here would return
    fixture data under a Jira label and make it impossible to tell whether a
    real connection is working. A workspace with no Jira gets a clear
    "nothing connected" instead.
    """
    if not workspace_id:
        return tool_result("search_jira", [], note="no Jira connection for this workspace")
    try:
        from app.services import jira_sync

        loop = asyncio.get_event_loop()
        if not await loop.run_in_executor(None, jira_sync.has_data, workspace_id):
            return tool_result("search_jira", [], note="no Jira issues indexed for this workspace yet")
        hits = await loop.run_in_executor(
            None, lambda: jira_sync.search(workspace_id, query, project_key, top_k)
        )
    except Exception as exc:  # noqa: BLE001
        log.error("tool.search_jira_error", error=str(exc))
        return tool_error("search_jira", f"search_jira failed: {exc}")

    evidence = [
        make_evidence(
            "ticket",
            # The issue key IS the locator people recognise, and the URL
            # rides along in the snippet so an answer can be followed up.
            f"jira:{h.get('issue_key')}",
            f"[{h.get('status')}] {h.get('summary')} — {h.get('text', '')[:600]}",
            float(h.get("score") or 0.0),
        )
        for h in hits
    ]
    return tool_result(
        "search_jira", evidence, note=None if evidence else f"no Jira issues matched '{query}'"
    )


async def _search_connector(provider: str, tool_name: str, query: str, top_k: int, workspace_id: str | None) -> dict:
    """Shared body for the connector-backed tools.

    Like search_jira and unlike search_slack, there is NO seed fallback:
    these sources have no fixture equivalent, so returning demo data under a
    Linear or Datadog label would make a broken connection indistinguishable
    from a working one.
    """
    if not workspace_id:
        return tool_result(tool_name, [], note=f"no {provider} connection for this workspace")
    try:
        from app.services.connectors import base as connector_base

        loop = asyncio.get_event_loop()
        if not await loop.run_in_executor(None, connector_base.has_data, workspace_id, provider):
            return tool_result(tool_name, [], note=f"no {provider} data indexed for this workspace yet")
        hits = await loop.run_in_executor(
            None, lambda: connector_base.search(workspace_id, provider, query, top_k)
        )
    except Exception as exc:  # noqa: BLE001
        log.error("tool.connector_search_error", provider=provider, error=str(exc))
        return tool_error(tool_name, f"{tool_name} failed: {exc}")

    evidence = [
        make_evidence(
            "ticket" if provider in ("linear",) else ("incident" if provider == "datadog" else "docs"),
            f"{provider}:{h.get('external_id')}",
            f"{h.get('title', '')} — {h.get('text', '')[:600]}",
            float(h.get("score") or 0.0),
        )
        for h in hits
    ]
    return tool_result(tool_name, evidence, note=None if evidence else f"nothing in {provider} matched '{query}'")


async def search_linear(query: str, top_k: int = 8, workspace_id: str | None = None) -> dict:
    return await _search_connector("linear", "search_linear", query, top_k, workspace_id)


async def search_notion(query: str, top_k: int = 8, workspace_id: str | None = None) -> dict:
    return await _search_connector("notion", "search_notion", query, top_k, workspace_id)


async def search_datadog(query: str, top_k: int = 8, workspace_id: str | None = None) -> dict:
    return await _search_connector("datadog", "search_datadog", query, top_k, workspace_id)


async def search_custom_docs(query: str, top_k: int = 8, workspace_id: str | None = None) -> dict:
    """Search documents the workspace uploaded — business flows, runbooks,
    escalation policies. The "what is our process for this" source."""
    return await _search_connector("custom_docs", "search_custom_docs", query, top_k, workspace_id)
