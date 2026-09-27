"""Tool schemas + dispatch. app.agent.loop (Phase 3) loads TOOL_SCHEMAS at
startup to build the planner's tool-calling prompt; app.routers.tools uses
dispatch() to execute a named tool from a JSON body — this is also the
Postman-style debug surface mentioned in the build plan.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.tools.code import find_usages, read_file, search_code, trace_symbol
from app.tools.conflict import check_conflict
from app.tools.memory import search_past_calls
from app.tools.knowledge import (
    search_custom_docs,
    search_datadog,
    search_docs,
    search_jira,
    search_linear,
    search_notion,
    search_slack,
    search_tickets,
)
from app.tools.provenance import explain_why
from app.tools.tenant import get_account_logs_tool, get_account_tool, get_incidents_tool, list_accounts_tool

ToolFn = Callable[..., Awaitable[dict]]

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "search_code",
        "description": "Semantic search over the ingested repo's source code.",
        "parameters": {
            "query": {"type": "string", "required": True},
            "repo_id": {"type": "string", "required": False},
            "top_k": {"type": "integer", "required": False, "default": 8},
        },
    },
    {
        "name": "trace_symbol",
        "description": "Trace a symbol's call/import relationships using the module graph plus semantic search.",
        "parameters": {
            "symbol": {"type": "string", "required": True},
            "repo_id": {"type": "string", "required": True},
        },
    },
    {
        "name": "find_usages",
        "description": "Find usages/references of a symbol across the codebase.",
        "parameters": {
            "symbol": {"type": "string", "required": True},
            "repo_id": {"type": "string", "required": False},
        },
    },
    {
        "name": "read_file",
        "description": "Read a specific file (optionally a line range) from an ingested repo.",
        "parameters": {
            "path": {"type": "string", "required": True},
            "repo_id": {"type": "string", "required": True},
            "start": {"type": "integer", "required": False},
            "end": {"type": "integer", "required": False},
        },
    },
    {
        "name": "search_docs",
        "description": "Semantic search over the product documentation.",
        "parameters": {
            "query": {"type": "string", "required": True},
            "top_k": {"type": "integer", "required": False, "default": 6},
        },
    },
    {
        "name": "search_tickets",
        "description": "Semantic search over support/internal tickets, optionally scoped to one account.",
        "parameters": {
            "query": {"type": "string", "required": True},
            "account_id": {"type": "string", "required": False},
            "top_k": {"type": "integer", "required": False, "default": 6},
        },
    },
    {
        "name": "search_jira",
        "description": (
            "Search connected Jira issues — whether something is a known bug, its status, "
            "and who owns it. Use for 'is this a known issue', 'is there a ticket for', "
            "'what's the status of'. Returns nothing if the workspace has no Jira connected."
        ),
        "parameters": {
            "query": {"type": "string", "required": True},
            "project_key": {"type": "string", "required": False},
            "top_k": {"type": "integer", "required": False, "default": 8},
        },
    },
    {
        "name": "search_custom_docs",
        "description": (
            "Search documents this workspace uploaded — business flows, operations notes, "
            "escalation policies. Use for 'what is our process for', 'who approves', "
            "'what are we supposed to do when'."
        ),
        "parameters": {"query": {"type": "string", "required": True},
                        "top_k": {"type": "integer", "required": False, "default": 8}},
    },
    {
        "name": "search_linear",
        "description": (
            "Search connected Linear issues — status of a fix, which cycle it lands in, and the "
            "discussion on it. Returns nothing if Linear isn't connected."
        ),
        "parameters": {"query": {"type": "string", "required": True},
                        "top_k": {"type": "integer", "required": False, "default": 8}},
    },
    {
        "name": "search_notion",
        "description": (
            "Search connected Notion pages — runbooks, internal docs and decisions kept outside "
            "the repo. Returns nothing if Notion isn't connected."
        ),
        "parameters": {"query": {"type": "string", "required": True},
                        "top_k": {"type": "integer", "required": False, "default": 8}},
    },
    {
        "name": "search_datadog",
        "description": (
            "Search connected Datadog monitors and incidents — whether something is alerting or "
            "on fire RIGHT NOW that explains what the customer is seeing. Returns nothing if "
            "Datadog isn't connected."
        ),
        "parameters": {"query": {"type": "string", "required": True},
                        "top_k": {"type": "integer", "required": False, "default": 8}},
    },
    {
        "name": "search_slack",
        "description": "Semantic search over internal Slack history, optionally scoped to one channel.",
        "parameters": {
            "query": {"type": "string", "required": True},
            "channel": {"type": "string", "required": False},
            "top_k": {"type": "integer", "required": False, "default": 8},
        },
    },
    {
        "name": "get_account",
        "description": "Look up a customer account's config (tier, webhook settings, etc). Secrets are redacted to last 4 chars.",
        "parameters": {"account_id": {"type": "string", "required": True}},
    },
    {
        "name": "list_accounts",
        "description": "List all known customer accounts.",
        "parameters": {},
    },
    {
        "name": "get_account_logs",
        "description": "Fetch recent log events for an account, optionally filtered by level and since a timestamp.",
        "parameters": {
            "account_id": {"type": "string", "required": True},
            "since": {"type": "string", "required": False, "description": "ISO8601 timestamp"},
            "level": {"type": "string", "required": False},
            "limit": {"type": "integer", "required": False, "default": 20},
        },
    },
    {
        "name": "get_incidents",
        "description": "List past incidents, optionally filtered by account or since a timestamp.",
        "parameters": {
            "since": {"type": "string", "required": False, "description": "ISO8601 timestamp"},
            "account_id": {"type": "string", "required": False},
        },
    },
    {
        "name": "search_past_calls",
        "description": (
            "Search what was asked and answered on this workspace's EARLIER calls. Use for "
            "'what did they ask last time', 'did we discuss this before', 'what did you tell "
            "them', 'we covered this on the last call'. Returns nothing if there are no "
            "earlier transcribed calls."
        ),
        "parameters": {
            "query": {"type": "string", "required": True},
            "top_k": {"type": "integer", "required": False, "default": 6},
        },
    },
    {
        "name": "explain_why",
        "description": (
            "Trace the 'why' behind a piece of code: joins code -> commit -> ticket -> PR -> Slack "
            "thread. Use this when asked why a business rule or piece of logic exists."
        ),
        "parameters": {
            "symbol_or_path": {"type": "string", "required": True},
            "repo_id": {"type": "string", "required": True},
        },
    },
    {
        "name": "check_conflict",
        "description": (
            "Check whether docs and code agree on a specific claim. Use this when a question touches "
            "a policy or behavior that might be documented differently than it's implemented."
        ),
        "parameters": {
            "claim": {"type": "string", "required": True},
            "repo_id": {"type": "string", "required": True},
        },
    },
]

_DISPATCH: dict[str, ToolFn] = {
    "search_code": search_code,
    "trace_symbol": trace_symbol,
    "find_usages": find_usages,
    "read_file": read_file,
    "search_docs": search_docs,
    "search_tickets": search_tickets,
    "search_slack": search_slack,
    "search_jira": search_jira,
    "search_custom_docs": search_custom_docs,
    "search_linear": search_linear,
    "search_notion": search_notion,
    "search_datadog": search_datadog,
    "search_past_calls": search_past_calls,
    "get_account": get_account_tool,
    "list_accounts": list_accounts_tool,
    "get_account_logs": get_account_logs_tool,
    "get_incidents": get_incidents_tool,
    "explain_why": explain_why,
    "check_conflict": check_conflict,
}


class UnknownToolError(Exception):
    pass


async def dispatch(tool_name: str, args: dict[str, Any]) -> dict:
    fn = _DISPATCH.get(tool_name)
    if fn is None:
        raise UnknownToolError(tool_name)
    return await fn(**args)


def list_tool_names() -> list[str]:
    return list(_DISPATCH.keys())
