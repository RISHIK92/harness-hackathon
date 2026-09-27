"""Which sources a workspace can actually use, and which tools each unlocks.

Two consumers, one definition:
  - the console, to render connection cards and their toggles;
  - the agent loop, to hide tools the workspace cannot use from the planner.

Hiding is not cosmetic. A planner offered `search_jira` for a workspace
with no Jira will sometimes call it, waste a round-trip, get nothing, and
occasionally conclude "there is no ticket for this" — which is a wrong
answer, not a missing one. Fewer, real tools produce better plans.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import (
    ConnectorProvider,
    CustomDoc,
    ExternalConnection,
    JiraConnection,
    Meeting,
    Repo,
    RepoStatus,
    SlackInstallation,
    TranscriptEntry,
)


@dataclass
class SourceGroup:
    key: str
    label: str
    tools: list[str]
    # Default-on sources are the ones every workspace has from day one and
    # that no one has to think about. Everything else appears only once it
    # is genuinely connected.
    default_enabled: bool = False
    available: bool = False
    detail: str = ""
    coming_soon: bool = False
    # True when `available` comes from the dashboard's Mock button
    # (routers/mock.py) rather than a real connection — the pre-call
    # screen uses this to offer "turn off" instead of "Connect".
    is_mock: bool = False


# The demo corpus (the fictional "Meridian" company) is a REHEARSAL fixture.
# It used to be appended to every workspace's tool list unconditionally,
# which meant a real customer's agent could answer from invented accounts,
# invented tickets and invented docs — confidently, and with citations that
# look exactly like real ones. It is now an ordinary source group: opt-in,
# off unless this deployment explicitly enables it.
SEED_TOOLS = ["search_docs", "search_tickets", "get_account", "list_accounts",
              "get_account_logs", "get_incidents", "check_conflict"]


def _groups() -> list[SourceGroup]:
    return [
        SourceGroup("github", "GitHub", ["search_code", "trace_symbol", "find_usages", "read_file", "explain_why"], default_enabled=True),
        SourceGroup("custom_docs", "Custom docs", ["search_custom_docs"], default_enabled=True),
        # The workspace's own earlier calls. Default-on because there is
        # nothing to connect — it is a record this product already keeps —
        # but `available` still gates it, so a workspace that has never
        # had a call is not offered a memory it does not have.
        SourceGroup("past_calls", "Past calls", ["search_past_calls"], default_enabled=True),
        SourceGroup("slack", "Slack", ["search_slack"]),
        SourceGroup("jira", "Jira", ["search_jira"]),
        SourceGroup("notion", "Notion", ["search_notion"]),
        SourceGroup("linear", "Linear", ["search_linear"]),
        SourceGroup("datadog", "Datadog", ["search_datadog"]),
        SourceGroup("outlook", "Outlook", [], coming_soon=True),
        SourceGroup("demo_corpus", "Demo corpus (Meridian)", SEED_TOOLS),
    ]


async def _false() -> bool:
    # A connection row already exists, so the has_data() check for it is
    # skipped entirely — this fills the same slot in the gather() below
    # without a wasted Qdrant round-trip.
    return False


async def source_groups(session: AsyncSession, workspace_id: str) -> list[SourceGroup]:
    groups = {g.key: g for g in _groups()}

    ready_repos = (await session.execute(
        select(Repo).where(Repo.workspace_id == workspace_id, Repo.status == RepoStatus.READY)
    )).scalars().all()
    groups["github"].available = bool(ready_repos)
    groups["github"].is_mock = bool(ready_repos) and all(r.is_mock for r in ready_repos)
    groups["github"].detail = (
        f"{len(ready_repos)} repo{'s' if len(ready_repos) != 1 else ''} indexed"
        if ready_repos else "no repositories indexed yet"
    )

    docs = (await session.execute(
        select(CustomDoc).where(CustomDoc.workspace_id == workspace_id)
    )).scalars().all()
    groups["custom_docs"].available = bool(docs)
    groups["custom_docs"].detail = (
        f"{len(docs)} document{'s' if len(docs) != 1 else ''}" if docs else "nothing uploaded yet"
    )

    past_call_lines = (await session.execute(
        select(TranscriptEntry.id)
        .join(Meeting, Meeting.id == TranscriptEntry.meeting_id)
        .where(Meeting.workspace_id == workspace_id)
        .limit(1)
    )).first()
    groups["past_calls"].available = past_call_lines is not None
    groups["past_calls"].detail = (
        "earlier calls transcribed" if past_call_lines is not None else "no earlier calls yet"
    )

    slack = (await session.execute(
        select(SlackInstallation).where(SlackInstallation.workspace_id == workspace_id)
    )).scalars().first()

    jira = (await session.execute(
        select(JiraConnection).where(JiraConnection.workspace_id == workspace_id)
    )).scalars().first()

    external = (await session.execute(
        select(ExternalConnection).where(ExternalConnection.workspace_id == workspace_id)
    )).scalars().all()
    by_provider = {c.provider: c for c in external}

    # A real connection ROW is the normal signal, but the dashboard's "Mock"
    # button (routers/mock.py) indexes fictional data straight into these
    # same collections with no connection row at all — so a call started
    # right after clicking it would otherwise see the source as
    # unavailable and never offer search_slack/search_jira/etc. to the
    # planner. has_data() is the same cheap workspace-scoped point check
    # the search_* tools already run at call time; asked here too so a
    # meeting's allowed_tools isn't quietly narrower than what the agent
    # can actually answer from.
    from app.services import jira_sync, slack_sync
    from app.services.connectors import base as connector_base

    def _run(fn, *args):
        return asyncio.get_event_loop().run_in_executor(None, fn, *args)

    mock_slack, mock_jira, mock_notion, mock_linear, mock_datadog = await asyncio.gather(
        _run(slack_sync.has_data, workspace_id) if slack is None else _false(),
        _run(jira_sync.has_data, workspace_id) if jira is None else _false(),
        _run(connector_base.has_data, workspace_id, "notion") if ConnectorProvider.NOTION not in by_provider else _false(),
        _run(connector_base.has_data, workspace_id, "linear") if ConnectorProvider.LINEAR not in by_provider else _false(),
        _run(connector_base.has_data, workspace_id, "datadog") if ConnectorProvider.DATADOG not in by_provider else _false(),
    )

    groups["slack"].available = slack is not None or mock_slack
    groups["slack"].is_mock = slack is None and mock_slack
    groups["slack"].detail = slack.team_name if slack else ("mock data (testing)" if mock_slack else "not connected")

    groups["jira"].available = jira is not None or mock_jira
    groups["jira"].is_mock = jira is None and mock_jira
    groups["jira"].detail = jira.site_url if jira else ("mock data (testing)" if mock_jira else "not connected")

    for provider, key, mocked in (
        (ConnectorProvider.NOTION, "notion", mock_notion),
        (ConnectorProvider.LINEAR, "linear", mock_linear),
        (ConnectorProvider.DATADOG, "datadog", mock_datadog),
    ):
        conn = by_provider.get(provider)
        groups[key].available = conn is not None or mocked
        groups[key].is_mock = conn is None and mocked
        groups[key].detail = (
            (conn.display_name or "connected") if conn else ("mock data (testing)" if mocked else "not connected")
        )

    # Only present on a deployment that opted in; otherwise the group is
    # dropped entirely so it cannot be toggled on by accident.
    from app.config import get_settings

    if get_settings().enable_demo_corpus:
        groups["demo_corpus"].available = True
        groups["demo_corpus"].detail = "fictional Meridian data, for demos"
    else:
        groups.pop("demo_corpus", None)

    return list(groups.values())


def default_enabled_keys(groups: list[SourceGroup]) -> list[str]:
    """What a new call starts with: the default-on sources, but only where
    they actually have data — offering a toggle for an empty source is a
    promise the agent cannot keep."""
    return [g.key for g in groups if g.default_enabled and g.available]


def tools_for(groups: list[SourceGroup], enabled_keys: list[str]) -> list[str]:
    """Tool names the planner may see for this call.

    Nothing is added implicitly. If a workspace has connected nothing, the
    planner gets an empty list and the agent abstains — which is the honest
    outcome, and far better than answering from a fixture.
    """
    enabled = set(enabled_keys)
    names: list[str] = []
    for group in groups:
        if group.key in enabled and group.available:
            names.extend(group.tools)
    return sorted(set(names))


def has_any_source(groups: list[SourceGroup]) -> bool:
    """Whether this workspace can answer anything at all yet."""
    return any(g.available and not g.coming_soon for g in groups)
