"""Resolve which repos belong to a workspace, for the agent loop's
multi-repo disambiguation (app.agent.loop / app.agent.prompts). Plain DB
reads, no transport imports — safe to call from app/agent/ per CLAUDE.md's
Section 5 boundary.
"""
from __future__ import annotations

from sqlmodel import select

from app.database import AsyncSessionLocal
from app.models import Repo, RepoStatus


async def list_ready_repos(workspace_id: str) -> list[dict]:
    """Repos in this workspace that have finished ingesting — the only
    ones a tool call could plausibly get evidence from. A repo that's
    still cloning/embedding or failed isn't a real disambiguation option
    yet, so it's left off the planner's known-repos list entirely rather
    than shown as a dead end."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Repo.id, Repo.name).where(
                Repo.workspace_id == workspace_id,
                Repo.status == RepoStatus.READY,
            )
        )
        return [{"id": rid, "name": name} for rid, name in result.all()]
