"""Jira: connect a site with an API token, choose projects, sync issues.

API token rather than OAuth 3LO on purpose — Atlassian's OAuth needs a
registered app with an HTTPS callback, the same wall Slack's distribution
hit. A token is created by any user from their Atlassian account in a
minute, works on localhost, and carries exactly that user's permissions:
this cannot see more of Jira than the person who connected it can.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_current_user
from app.core.crypto import decrypt, encrypt
from app.core.workspace import get_current_workspace, require_role
from app.database import get_session
from app.models import (
    ConnectionScope,
    JiraConnection,
    JiraConnectionRead,
    JiraProject,
    User,
    Workspace,
    WorkspaceRole,
)
from app.services import jira_sync

log = structlog.get_logger()
router = APIRouter()


class ConnectBody(SQLModel):
    site_url: str
    account_email: str
    api_token: str
    # Defaults to workspace-wide because that is what a support agent needs;
    # a user may keep it private instead (ConnectionScope.USER).
    scope: ConnectionScope = ConnectionScope.WORKSPACE


@router.post("", response_model=JiraConnectionRead, status_code=201)
async def connect(
    payload: ConnectBody,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(require_role(WorkspaceRole.OWNER)),
):
    site = payload.site_url.strip().rstrip("/")
    if not site.startswith("http"):
        site = f"https://{site}"

    # Verified BEFORE the token is stored: an unverified credential fails
    # later, in a background sync, where nobody is watching.
    try:
        me = await _run(jira_sync.verify, site, payload.account_email.strip(), payload.api_token.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not reach that Jira site: {exc}")

    result = await session.execute(
        select(JiraConnection).where(
            JiraConnection.workspace_id == workspace.id, JiraConnection.site_url == site
        )
    )
    conn = result.scalars().first()
    if conn:
        conn.api_token_encrypted = encrypt(payload.api_token.strip())
        conn.account_email = payload.account_email.strip()
        conn.display_name = me.get("display_name")
    else:
        conn = JiraConnection(
            workspace_id=workspace.id,
            site_url=site,
            account_email=payload.account_email.strip(),
            api_token_encrypted=encrypt(payload.api_token.strip()),
            display_name=me.get("display_name"),
            scope=payload.scope,
            owner_user_id=current_user.id if payload.scope == ConnectionScope.USER else None,
            connected_by=current_user.id,
        )
    session.add(conn)
    await session.commit()
    await session.refresh(conn)
    log.info("jira.connected", site=site, workspace_id=workspace.id)
    return conn


@router.get("", response_model=list[JiraConnectionRead])
async def list_connections(
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
):
    result = await session.execute(
        select(JiraConnection).where(JiraConnection.workspace_id == workspace.id)
    )
    return result.scalars().all()


async def _run(fn, *args):
    """Jira's client is sync (httpx.Client); keep it off the event loop."""
    import asyncio

    return await asyncio.get_event_loop().run_in_executor(None, lambda: fn(*args))


async def _connection(session: AsyncSession, connection_id: str, workspace: Workspace) -> JiraConnection:
    conn = await session.get(JiraConnection, connection_id)
    if not conn or conn.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Jira connection not found")
    return conn


def _token(conn: JiraConnection) -> str:
    token = decrypt(conn.api_token_encrypted)
    if not token:
        raise HTTPException(status_code=409, detail="This Jira token can no longer be decrypted — reconnect Jira")
    return token


@router.get("/{connection_id}/projects")
async def projects(
    connection_id: str,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
):
    conn = await _connection(session, connection_id, workspace)
    available = await _run(jira_sync.list_projects, conn.site_url, conn.account_email, _token(conn))
    chosen = await session.execute(
        select(JiraProject.project_key).where(
            JiraProject.connection_id == conn.id, JiraProject.selected == True  # noqa: E712
        )
    )
    selected = {k for (k,) in chosen.all()}
    return {
        "connection": JiraConnectionRead.model_validate(conn),
        "projects": [{**p, "selected": p["key"] in selected} for p in available],
    }


class SelectProjectsBody(SQLModel):
    project_keys: list[str] = []


@router.post("/{connection_id}/projects")
async def select_projects(
    connection_id: str,
    payload: SelectProjectsBody,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(require_role(WorkspaceRole.MEMBER)),
):
    conn = await _connection(session, connection_id, workspace)
    available = {p["key"]: p for p in await _run(jira_sync.list_projects, conn.site_url, conn.account_email, _token(conn))}

    existing = await session.execute(select(JiraProject).where(JiraProject.connection_id == conn.id))
    by_key = {p.project_key: p for p in existing.scalars().all()}

    for key in payload.project_keys:
        row = by_key.get(key)
        if row:
            row.selected = True
        else:
            row = JiraProject(
                connection_id=conn.id,
                workspace_id=workspace.id,
                project_key=key,
                name=(available.get(key) or {}).get("name", key),
            )
        session.add(row)
    for key, row in by_key.items():
        if key not in payload.project_keys:
            row.selected = False
            session.add(row)
    await session.commit()

    from app.tasks.jira_ingest import sync_jira

    sync_jira.apply_async(args=[conn.id])
    return {"selected": payload.project_keys, "syncing": True}


@router.post("/{connection_id}/sync")
async def resync(
    connection_id: str,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(require_role(WorkspaceRole.MEMBER)),
):
    conn = await _connection(session, connection_id, workspace)
    from app.tasks.jira_ingest import sync_jira

    sync_jira.apply_async(args=[conn.id])
    return {"syncing": True}
