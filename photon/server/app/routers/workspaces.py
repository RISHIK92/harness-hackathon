"""Workspace CRUD. Membership is the authorisation unit — see
app/core/workspace.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

import secrets
from datetime import datetime

from fastapi import HTTPException
from sqlmodel import SQLModel

from app.core.auth import get_current_user
from app.core.workspace import (
    ensure_personal_workspace,
    get_current_workspace,
    membership_for,
    require_role,
)
from app.database import get_session
from app.models import (
    JoinRequestRead,
    WorkspaceKind,
    JoinRequestStatus,
    User,
    Workspace,
    WorkspaceCreate,
    WorkspaceInvite,
    WorkspaceJoinRequest,
    WorkspaceMember,
    WorkspaceMemberRead,
    WorkspaceRead,
    WorkspaceRole,
    WorkspaceUpdate,
)

router = APIRouter()


@router.get("", response_model=list[WorkspaceRead])
async def list_workspaces(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    # Guarantees a first-time user always sees at least one workspace
    # instead of an empty picker they cannot get out of.
    await ensure_personal_workspace(session, current_user)
    result = await session.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == current_user.id)
        .order_by(Workspace.created_at)
    )
    return [
        WorkspaceRead(
            id=w.id, name=w.name, kind=w.kind, agent_name=w.agent_name,
            is_personal=w.is_personal, org_agent_enabled=w.org_agent_enabled, org_agent_sources=w.org_agent_sources, role=role,
            created_at=w.created_at,
        )
        for w, role in result.all()
    ]


@router.post("", response_model=WorkspaceRead, status_code=201)
async def create_workspace(
    payload: WorkspaceCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    workspace = Workspace(
        name=payload.name.strip(),
        kind=payload.kind,
        agent_name=(payload.agent_name or "").strip() or None,
        is_personal=False,
    )
    session.add(workspace)
    await session.flush()
    session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=current_user.id, role=WorkspaceRole.OWNER)
    )
    await session.commit()
    await session.refresh(workspace)
    return WorkspaceRead(
        id=workspace.id,
        name=workspace.name,
        kind=workspace.kind,
        agent_name=workspace.agent_name,
        is_personal=workspace.is_personal,
        org_agent_enabled=workspace.org_agent_enabled,
        org_agent_sources=workspace.org_agent_sources,
        role=WorkspaceRole.OWNER,
        created_at=workspace.created_at,
    )


@router.get("/current", response_model=WorkspaceRead)
async def current_workspace(
    workspace: Workspace = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    from app.core.workspace import membership_for

    member = await membership_for(session, workspace.id, current_user.id)
    return WorkspaceRead(
        id=workspace.id,
        name=workspace.name,
        kind=workspace.kind,
        agent_name=workspace.agent_name,
        is_personal=workspace.is_personal,
        org_agent_enabled=workspace.org_agent_enabled,
        org_agent_sources=workspace.org_agent_sources,
        role=member.role if member else WorkspaceRole.OWNER,
        created_at=workspace.created_at,
    )


# ── Invites and joining ──────────────────────────────────────────────────
# The code proves someone was pointed at this workspace; an owner's approval
# is what actually grants access. Two steps on purpose: a link that lands in
# the wrong chat should not hand a stranger a company's private source.


def _new_code() -> str:
    # URL-safe and short enough to read aloud on a call, long enough that
    # guessing is not a strategy (~48 bits).
    return secrets.token_urlsafe(9)


@router.get("/invite")
async def get_invite(
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(require_role(WorkspaceRole.OWNER)),
):
    result = await session.execute(
        select(WorkspaceInvite)
        .where(WorkspaceInvite.workspace_id == workspace.id, WorkspaceInvite.revoked_at.is_(None))
        .order_by(WorkspaceInvite.created_at.desc())
    )
    invite = result.scalars().first()
    return {"code": invite.code if invite else None}


@router.post("/invite")
async def rotate_invite(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(require_role(WorkspaceRole.OWNER)),
):
    """Issue a code, revoking any previous one.

    Rotation is the whole point: "the old link stops working" must be one
    obvious action rather than an audit of accumulated codes.
    """
    existing = await session.execute(
        select(WorkspaceInvite).where(
            WorkspaceInvite.workspace_id == workspace.id, WorkspaceInvite.revoked_at.is_(None)
        )
    )
    for old in existing.scalars().all():
        old.revoked_at = datetime.utcnow()
        session.add(old)

    if workspace.kind == WorkspaceKind.INDIVIDUAL:
        # Refused rather than silently upgraded: turning someone's private
        # workspace into a shared one is not a side effect of clicking
        # "invite", and every connector in it would become readable by
        # whoever accepts.
        raise HTTPException(
            status_code=409,
            detail="This is an individual workspace — create a team workspace to invite people",
        )

    invite = WorkspaceInvite(workspace_id=workspace.id, code=_new_code(), created_by=current_user.id)
    session.add(invite)
    await session.commit()
    return {"code": invite.code}


@router.delete("/invite", status_code=204)
async def revoke_invite(
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(require_role(WorkspaceRole.OWNER)),
):
    result = await session.execute(
        select(WorkspaceInvite).where(
            WorkspaceInvite.workspace_id == workspace.id, WorkspaceInvite.revoked_at.is_(None)
        )
    )
    for invite in result.scalars().all():
        invite.revoked_at = datetime.utcnow()
        session.add(invite)
    await session.commit()


class JoinRequestBody(SQLModel):
    code: str


@router.post("/join")
async def request_to_join(
    payload: JoinRequestBody,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Redeem a code — which creates a PENDING request, not a membership."""
    result = await session.execute(
        select(WorkspaceInvite).where(
            WorkspaceInvite.code == payload.code.strip(), WorkspaceInvite.revoked_at.is_(None)
        )
    )
    invite = result.scalars().first()
    if not invite:
        # Deliberately identical for "never existed" and "revoked": a
        # distinction would let someone probe which codes were once real.
        raise HTTPException(status_code=404, detail="That invite code is not valid")

    workspace = await session.get(Workspace, invite.workspace_id)
    if await membership_for(session, invite.workspace_id, current_user.id):
        return {"status": "already_member", "workspace": {"id": workspace.id, "name": workspace.name}}

    existing = await session.execute(
        select(WorkspaceJoinRequest).where(
            WorkspaceJoinRequest.workspace_id == invite.workspace_id,
            WorkspaceJoinRequest.user_id == current_user.id,
            WorkspaceJoinRequest.status == JoinRequestStatus.PENDING,
        )
    )
    if existing.scalars().first():
        return {"status": "pending", "workspace": {"id": workspace.id, "name": workspace.name}}

    session.add(WorkspaceJoinRequest(workspace_id=invite.workspace_id, user_id=current_user.id))
    await session.commit()
    return {"status": "pending", "workspace": {"id": workspace.id, "name": workspace.name}}


@router.get("/requests", response_model=list[JoinRequestRead])
async def list_join_requests(
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(require_role(WorkspaceRole.OWNER)),
):
    result = await session.execute(
        select(WorkspaceJoinRequest, User.email)
        .join(User, User.id == WorkspaceJoinRequest.user_id)
        .where(
            WorkspaceJoinRequest.workspace_id == workspace.id,
            WorkspaceJoinRequest.status == JoinRequestStatus.PENDING,
        )
        .order_by(WorkspaceJoinRequest.requested_at)
    )
    return [
        JoinRequestRead(
            id=r.id, user_id=r.user_id, email=email, status=r.status, requested_at=r.requested_at
        )
        for r, email in result.all()
    ]


class DecideRequestBody(SQLModel):
    approve: bool
    role: WorkspaceRole = WorkspaceRole.MEMBER


@router.post("/requests/{request_id}")
async def decide_join_request(
    request_id: str,
    payload: DecideRequestBody,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(require_role(WorkspaceRole.OWNER)),
):
    join_request = await session.get(WorkspaceJoinRequest, request_id)
    if not join_request or join_request.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Request not found")
    if join_request.status != JoinRequestStatus.PENDING:
        raise HTTPException(status_code=409, detail="That request has already been decided")

    join_request.status = JoinRequestStatus.APPROVED if payload.approve else JoinRequestStatus.REJECTED
    join_request.decided_at = datetime.utcnow()
    join_request.decided_by = current_user.id

    if payload.approve:
        # Guard against a double-approve racing in and creating two
        # memberships for the same person.
        if not await membership_for(session, workspace.id, join_request.user_id):
            join_request.granted_role = payload.role
            session.add(
                WorkspaceMember(
                    workspace_id=workspace.id, user_id=join_request.user_id, role=payload.role
                )
            )
    session.add(join_request)
    await session.commit()
    return {"status": join_request.status}


@router.get("/members", response_model=list[WorkspaceMemberRead])
async def list_members(
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(require_role(WorkspaceRole.VIEWER)),
):
    result = await session.execute(
        select(WorkspaceMember, User.email)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace.id)
        .order_by(WorkspaceMember.created_at)
    )
    return [
        WorkspaceMemberRead(user_id=m.user_id, email=email, role=m.role, joined_at=m.created_at)
        for m, email in result.all()
    ]


class ChangeRoleBody(SQLModel):
    role: WorkspaceRole


@router.patch("/members/{user_id}")
async def change_member_role(
    user_id: str,
    payload: ChangeRoleBody,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(require_role(WorkspaceRole.OWNER)),
):
    member = await membership_for(session, workspace.id, user_id)
    if not member:
        raise HTTPException(status_code=404, detail="Not a member of this workspace")
    if member.role == WorkspaceRole.OWNER and payload.role != WorkspaceRole.OWNER:
        await _guard_last_owner(session, workspace.id, user_id)
    member.role = payload.role
    session.add(member)
    await session.commit()
    return {"user_id": user_id, "role": member.role}


@router.delete("/members/{user_id}", status_code=204)
async def remove_member(
    user_id: str,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(require_role(WorkspaceRole.OWNER)),
):
    member = await membership_for(session, workspace.id, user_id)
    if not member:
        raise HTTPException(status_code=404, detail="Not a member of this workspace")
    await _guard_last_owner(session, workspace.id, user_id)
    await session.delete(member)
    await session.commit()


async def _guard_last_owner(session: AsyncSession, workspace_id: str, user_id: str) -> None:
    """A workspace with no owner can never be administered again — nobody
    could invite, approve, or connect anything. Refuse rather than create
    an unrecoverable state."""
    result = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.role == WorkspaceRole.OWNER,
        )
    )
    owners = result.scalars().all()
    if len(owners) <= 1 and any(o.user_id == user_id for o in owners):
        raise HTTPException(
            status_code=409, detail="This is the last owner — promote someone else first"
        )


@router.patch("/settings", response_model=WorkspaceRead)
async def update_workspace(
    payload: WorkspaceUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(require_role(WorkspaceRole.OWNER)),
):
    """Rename the workspace, or name its agent.

    The agent's name is not decoration: it is what the agent calls itself
    in every answer, what it announces on joining, and the wake word people
    say out loud. "Ask Photon" is a stranger's name in someone else's
    company.
    """
    if payload.name is not None and payload.name.strip():
        workspace.name = payload.name.strip()
    if payload.agent_name is not None:
        # Empty string means "back to the default", which is different from
        # not sending the field at all.
        workspace.agent_name = payload.agent_name.strip()[:40] or None
    if payload.org_agent_enabled is not None:
        # Owner-only by the dependency above: this lets an agent speak for
        # the whole company, from company-wide sources.
        workspace.org_agent_enabled = payload.org_agent_enabled
    if payload.org_agent_sources is not None:
        workspace.org_agent_sources = [k for k in payload.org_agent_sources if isinstance(k, str)][:30]
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)

    member = await membership_for(session, workspace.id, current_user.id)
    return WorkspaceRead(
        id=workspace.id, name=workspace.name, kind=workspace.kind,
        agent_name=workspace.agent_name, is_personal=workspace.is_personal,
        org_agent_enabled=workspace.org_agent_enabled,
        org_agent_sources=workspace.org_agent_sources,
        role=member.role if member else WorkspaceRole.OWNER, created_at=workspace.created_at,
    )
