"""Repo-scoped authorization for routes addressed by repo, job or pin id.

The original codebase-intelligence routes (graph, files, jobs, annotations,
learning path, the progress WebSocket) took a repo id from the URL and
served that repo to anyone — no login, no workspace check. They are mounted
behind `require_repo_access` now, a router-level dependency that resolves
the repo the request is about and 404s unless the caller's workspace owns it.

404 rather than 403, same as get_current_workspace: someone outside the
workspace should not learn that a repo id exists.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_current_user
from app.core.workspace import get_current_workspace
from app.database import get_session
from app.models import Job, Pin, Repo, User, Workspace


def may_access(repo: Repo, user: User, workspace: Workspace) -> bool:
    """Workspace membership is the rule; owner_id is a legacy fallback.

    Repos created before workspaces existed have workspace_id = NULL, and
    silently 404ing someone's own repo after an upgrade would look like
    data loss. Once those are backfilled this fallback can go.
    """
    if repo.workspace_id:
        return repo.workspace_id == workspace.id
    return repo.owner_id == user.id


_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")


async def authorize_repo(session: AsyncSession, repo_id: Optional[str], user: User,
                         workspace: Workspace) -> Repo:
    repo = await session.get(Repo, repo_id) if repo_id else None
    if repo is None or not may_access(repo, user, workspace):
        raise _NOT_FOUND
    return repo


async def require_repo_access(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
) -> Optional[Repo]:
    """Router-level dependency. Resolves the repo from `repo_id`, or through
    `job_id` / `pin_id`, in the path. A route with none of those (creating a
    pin, whose repo is in the body) still gets an authenticated user and a
    verified workspace, and must call `authorize_repo` itself."""
    params = request.path_params
    repo_id = params.get("repo_id")
    if repo_id is None and "job_id" in params:
        job = await session.get(Job, params["job_id"])
        repo_id = job.repo_id if job else None
        if repo_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if repo_id is None and "pin_id" in params:
        pin = await session.get(Pin, params["pin_id"])
        repo_id = pin.repo_id if pin else None
        if repo_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pin not found")
    if repo_id is None:
        return None
    return await authorize_repo(session, repo_id, current_user, workspace)
