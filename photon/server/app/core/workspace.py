"""Workspace resolution and membership checks — the tenant boundary.

Every request that touches customer data must resolve to a workspace the
caller actually belongs to. `get_current_workspace` is that single choke
point; routers depend on it rather than re-deriving ownership, so there is
one place to audit rather than one per endpoint.

Resolution order:
  1. an explicit `X-Workspace-Id` header (or `workspace_id` query param),
     which is what the UI sends once a workspace is selected;
  2. otherwise the caller's personal workspace, so every existing
     single-user flow keeps working without sending anything new.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, Query, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_current_user
from app.database import get_session
from app.models import User, Workspace, WorkspaceMember, WorkspaceRole


async def ensure_personal_workspace(session: AsyncSession, user: User) -> Workspace:
    """Every user has exactly one personal workspace, created on demand.

    Called from signup AND login: users who predate workspaces would
    otherwise authenticate successfully and then have nowhere to put data.
    """
    result = await session.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id, Workspace.is_personal == True)  # noqa: E712
    )
    existing = result.scalars().first()
    if existing:
        return existing

    workspace = Workspace(name=f"{user.email.split('@')[0]}'s workspace", is_personal=True)
    session.add(workspace)
    await session.flush()
    session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.OWNER)
    )
    await session.commit()
    await session.refresh(workspace)
    return workspace


async def membership_for(
    session: AsyncSession, workspace_id: str, user_id: str
) -> Optional[WorkspaceMember]:
    result = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    return result.scalars().first()


async def get_current_workspace(
    x_workspace_id: Optional[str] = Header(default=None, alias="X-Workspace-Id"),
    workspace_id: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Workspace:
    requested = x_workspace_id or workspace_id
    if not requested:
        return await ensure_personal_workspace(session, current_user)

    if not await membership_for(session, requested, current_user.id):
        # 404 rather than 403 on purpose: a non-member should not be able to
        # discover that a workspace id exists at all.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    workspace = await session.get(Workspace, requested)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace


# ── Roles ────────────────────────────────────────────────────────────────
# Ordered, so checks read as "at least MEMBER" rather than enumerating every
# role that qualifies — which is what quietly goes wrong when a role is
# added later and someone forgets one of the lists.
_ROLE_ORDER = {WorkspaceRole.VIEWER: 0, WorkspaceRole.MEMBER: 1, WorkspaceRole.OWNER: 2}


def role_at_least(role: WorkspaceRole, minimum: WorkspaceRole) -> bool:
    return _ROLE_ORDER.get(role, -1) >= _ROLE_ORDER[minimum]


def require_role(minimum: WorkspaceRole):
    """FastAPI dependency factory: 403 unless the caller holds `minimum`.

    403 here, not 404: the caller is a known member of a workspace they can
    already see, so hiding its existence buys nothing and an honest "you
    need a higher role" is far easier to act on. Non-members are still 404'd
    earlier by get_current_workspace.
    """

    async def dependency(
        workspace: Workspace = Depends(get_current_workspace),
        session: AsyncSession = Depends(get_session),
        current_user: User = Depends(get_current_user),
    ) -> Workspace:
        member = await membership_for(session, workspace.id, current_user.id)
        if not member or not role_at_least(member.role, minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action needs the {minimum.value} role in this workspace",
            )
        return workspace

    return dependency
