"""'Mock' sources — one click per provider to give a real, empty workspace
something to answer from, for a workspace's own testing rather than a real
vendor connection.

Deliberately separate from app/seed/ (the Meridian corpus the eval harness
depends on) and from the real connector routers: this is fictional
"Adventa" data, generated fresh per provider (app/mock/loader.py), and
every item is prefixed "[MOCK]" / given a "mock:" locator so it can never
be mistaken for a real GitHub/Slack/Jira/Linear/Notion/Datadog connection
— see CLAUDE.md's "Removing the demo corpus from real workspaces" section
for why that distinction actually matters here, not just as a nicety.

Indexed through the SAME sync/search code real connections use
(slack_sync.index_messages, jira_sync.index_issues, connector_base.index_items,
the normal repo-ingestion pipeline for GitHub) — so search_code/
search_slack/search_jira/search_linear/search_notion/search_datadog all
pick this up with no tool-code changes at all; they already query by
workspace_id.
"""
from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.workspace import require_role
from app.database import get_session
from app.mock import loader
from app.models import Job, Repo, RepoRead, RepoSourceType, RepoStatus, Workspace, WorkspaceRole
from app.tasks.ingestion import run_ingestion

log = structlog.get_logger()
router = APIRouter()

_PROVIDERS = ("github", "slack", "jira", "linear", "notion", "datadog")


async def _run(fn, *args):
    return await asyncio.get_event_loop().run_in_executor(None, fn, *args)


@router.get("")
async def mock_status(
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(require_role(WorkspaceRole.VIEWER)),
):
    """Which providers already have mock data for this workspace."""
    result = await session.execute(
        select(Repo).where(Repo.workspace_id == workspace.id, Repo.is_mock == True)  # noqa: E712
    )
    github_repo = result.scalars().first()

    from app.services import jira_sync, slack_sync
    from app.services.connectors import base as connector_base

    has_slack, has_jira, has_linear, has_notion, has_datadog = await asyncio.gather(
        _run(slack_sync.has_data, workspace.id),
        _run(jira_sync.has_data, workspace.id),
        _run(connector_base.has_data, workspace.id, "linear"),
        _run(connector_base.has_data, workspace.id, "notion"),
        _run(connector_base.has_data, workspace.id, "datadog"),
    )
    return {
        "github": github_repo is not None,
        "slack": has_slack,
        "jira": has_jira,
        "linear": has_linear,
        "notion": has_notion,
        "datadog": has_datadog,
    }


@router.post("/{provider}")
async def enable_mock(
    provider: str,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(require_role(WorkspaceRole.MEMBER)),
):
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=404, detail=f"No mock source called {provider!r}")

    if provider == "github":
        existing = await session.execute(
            select(Repo).where(Repo.workspace_id == workspace.id, Repo.is_mock == True)  # noqa: E712
        )
        repo = existing.scalars().first()
        if repo:
            return {"provider": "github", "repo_id": repo.id, "already_enabled": True}

        repo = Repo(
            name="Adventa backend [MOCK]",
            source_type=RepoSourceType.LOCAL,
            source_url=loader.mock_repo_path(),
            workspace_id=workspace.id,
            owner_id=None,
            is_mock=True,
        )
        session.add(repo)
        await session.commit()
        await session.refresh(repo)

        job = Job(repo_id=repo.id)
        session.add(job)
        await session.commit()
        await session.refresh(job)

        run_ingestion.apply_async(args=[repo.id, job.id], task_id=job.id)
        log.info("mock.enabled", provider="github", workspace_id=workspace.id, repo_id=repo.id)
        return {"provider": "github", "repo_id": repo.id, "already_enabled": False}

    if provider == "slack":
        from app.services import slack_sync

        messages, names = loader.mock_slack_messages()
        count = await _run(
            lambda: slack_sync.index_messages(workspace.id, "mock-finance", "finance", messages, names)
        )
    elif provider == "jira":
        from app.services import jira_sync

        count = await _run(lambda: jira_sync.index_issues(workspace.id, "ADV", loader.mock_jira_issues()))
    else:
        from app.services.connectors import base as connector_base

        items = {
            "linear": loader.mock_linear_items,
            "notion": loader.mock_notion_items,
            "datadog": loader.mock_datadog_items,
        }[provider]()
        count = await _run(lambda: connector_base.index_items(workspace.id, provider, "mock", items))

    log.info("mock.enabled", provider=provider, workspace_id=workspace.id, items=count)
    return {"provider": provider, "items_indexed": count, "already_enabled": False}


@router.delete("/{provider}")
async def disable_mock(
    provider: str,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(require_role(WorkspaceRole.MEMBER)),
):
    """Turn a mock source back off. Same removal shape as custom_docs'
    delete: the DB/Qdrant side is removed so the agent stops being able to
    cite it, not just hidden from the dashboard."""
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=404, detail=f"No mock source called {provider!r}")

    if provider == "github":
        result = await session.execute(
            select(Repo).where(Repo.workspace_id == workspace.id, Repo.is_mock == True)  # noqa: E712
        )
        repo = result.scalars().first()
        if repo:
            await session.delete(repo)
            await session.commit()
        log.info("mock.disabled", provider="github", workspace_id=workspace.id)
        return {"provider": "github", "removed": repo is not None}

    from qdrant_client.models import FieldCondition, Filter, MatchValue

    from app.core.embedding.embedder import get_qdrant

    if provider == "slack":
        from app.services import slack_sync

        await _run(slack_sync.ensure_collection)
        must = [
            FieldCondition(key="workspace_id", match=MatchValue(value=workspace.id)),
            FieldCondition(key="channel_id", match=MatchValue(value="mock-finance")),
        ]
        collection = slack_sync.COLLECTION
    elif provider == "jira":
        from app.services import jira_sync

        await _run(jira_sync.ensure_collection)
        must = [
            FieldCondition(key="workspace_id", match=MatchValue(value=workspace.id)),
            FieldCondition(key="project_key", match=MatchValue(value="ADV")),
        ]
        collection = jira_sync.COLLECTION
    else:
        from app.services.connectors import base as connector_base

        await _run(connector_base.ensure_collection)
        must = [
            FieldCondition(key="workspace_id", match=MatchValue(value=workspace.id)),
            FieldCondition(key="provider", match=MatchValue(value=provider)),
            FieldCondition(key="resource_id", match=MatchValue(value="mock")),
        ]
        collection = connector_base.COLLECTION

    await _run(
        lambda: get_qdrant().delete(collection_name=collection, points_selector=Filter(must=must))
    )
    log.info("mock.disabled", provider=provider, workspace_id=workspace.id)
    return {"provider": provider, "removed": True}
