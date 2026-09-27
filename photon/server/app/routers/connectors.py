"""One router for every simple connector (Linear, Notion, Datadog).

Slack and Jira have bespoke routers because each has real structure worth
modelling. These three share a shape — credentials in, resources listed,
resources selected, sync queued — so they share an endpoint set. A new
provider is a module in services/connectors/, not another router.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

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
    ConnectorProvider,
    ConnectorResource,
    ExternalConnection,
    ExternalConnectionRead,
    User,
    Workspace,
    WorkspaceRole,
)
from app.services.connectors import datadog, linear, notion

log = structlog.get_logger()
router = APIRouter()

ADAPTERS = {
    ConnectorProvider.LINEAR: linear,
    ConnectorProvider.NOTION: notion,
    ConnectorProvider.DATADOG: datadog,
}

# What each provider needs, so the UI can render the right form and the API
# can reject a half-filled one before touching the vendor.
CREDENTIAL_FIELDS: dict[ConnectorProvider, list[dict[str, Any]]] = {
    ConnectorProvider.LINEAR: [
        {"key": "api_key", "label": "Personal API key", "secret": True,
         "help": "Linear → Settings → Security & access → Personal API keys"},
    ],
    ConnectorProvider.NOTION: [
        {"key": "token", "label": "Internal integration token", "secret": True,
         "help": "notion.so/my-integrations → New integration. Then SHARE each page with it — "
                 "Notion shows an integration nothing until a page is shared."},
    ],
    ConnectorProvider.DATADOG: [
        {"key": "api_key", "label": "API key", "secret": True, "help": "Organization Settings → API Keys"},
        {"key": "app_key", "label": "Application key", "secret": True, "help": "Organization Settings → Application Keys"},
        {"key": "site", "label": "Site", "secret": False, "config": True,
         "help": "datadoghq.com, datadoghq.eu, us3.datadoghq.com … the wrong one returns 403"},
    ],
}


def _adapter(provider: ConnectorProvider):
    adapter = ADAPTERS.get(provider)
    if not adapter:
        raise HTTPException(status_code=404, detail=f"Unknown connector '{provider}'")
    return adapter


async def _run(fn, *args):
    return await asyncio.get_event_loop().run_in_executor(None, lambda: fn(*args))


def _creds(conn: ExternalConnection) -> dict:
    raw = decrypt(conn.credentials_encrypted)
    if not raw:
        raise HTTPException(
            status_code=409,
            detail=f"This {conn.provider.value} connection can no longer be decrypted — reconnect it",
        )
    return json.loads(raw)


@router.get("/providers")
async def providers(current_user: User = Depends(get_current_user)):
    """What can be connected and what each one asks for."""
    return {
        p.value: {"fields": CREDENTIAL_FIELDS[p]} for p in ADAPTERS
    }


class ConnectBody(SQLModel):
    credentials: dict = {}
    config: dict = {}
    scope: ConnectionScope = ConnectionScope.WORKSPACE


@router.post("/{provider}", response_model=ExternalConnectionRead, status_code=201)
async def connect(
    provider: ConnectorProvider,
    payload: ConnectBody,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(require_role(WorkspaceRole.OWNER)),
):
    adapter = _adapter(provider)
    required = [f["key"] for f in CREDENTIAL_FIELDS[provider] if f.get("secret")]
    missing = [k for k in required if not (payload.credentials.get(k) or "").strip()]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing: {', '.join(missing)}")

    # Verified before storage, like Jira: an unverified credential fails
    # later in a background sync where nobody sees it.
    try:
        info = await _run(adapter.verify, payload.credentials, payload.config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not reach {provider.value}: {exc}")

    result = await session.execute(
        select(ExternalConnection).where(
            ExternalConnection.workspace_id == workspace.id,
            ExternalConnection.provider == provider,
        )
    )
    conn = result.scalars().first()
    if not conn:
        conn = ExternalConnection(
            workspace_id=workspace.id, provider=provider, connected_by=current_user.id
        )
    conn.credentials_encrypted = encrypt(json.dumps(payload.credentials))
    conn.config = payload.config or {}
    conn.display_name = info.get("display_name", provider.value)
    conn.scope = payload.scope
    conn.owner_user_id = current_user.id if payload.scope == ConnectionScope.USER else None
    session.add(conn)
    await session.commit()
    await session.refresh(conn)
    log.info("connector.connected", provider=provider.value, workspace_id=workspace.id)
    return conn


@router.get("", response_model=list[ExternalConnectionRead])
async def list_connections(
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
):
    result = await session.execute(
        select(ExternalConnection).where(ExternalConnection.workspace_id == workspace.id)
    )
    return result.scalars().all()


async def _connection(session: AsyncSession, connection_id: str, workspace: Workspace) -> ExternalConnection:
    conn = await session.get(ExternalConnection, connection_id)
    if not conn or conn.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Connection not found")
    return conn


@router.get("/{connection_id}/resources")
async def resources(
    connection_id: str,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
):
    conn = await _connection(session, connection_id, workspace)
    adapter = _adapter(conn.provider)
    try:
        available = await _run(adapter.list_resources, _creds(conn), conn.config)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{conn.provider.value} error: {exc}")

    chosen = await session.execute(
        select(ConnectorResource.resource_id).where(
            ConnectorResource.connection_id == conn.id,
            ConnectorResource.selected == True,  # noqa: E712
        )
    )
    selected = {r for (r,) in chosen.all()}
    return {
        "connection": ExternalConnectionRead.model_validate(conn),
        "resources": [{**r, "selected": r["id"] in selected} for r in available],
        # Notion in particular is commonly empty here, and that is not a
        # failure — the integration sees nothing until pages are shared.
        "note": (
            "Nothing here yet? Share the pages with your Notion integration first."
            if conn.provider == ConnectorProvider.NOTION and not available
            else None
        ),
    }


class SelectBody(SQLModel):
    resource_ids: list[str] = []


@router.post("/{connection_id}/resources")
async def select_resources(
    connection_id: str,
    payload: SelectBody,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(require_role(WorkspaceRole.MEMBER)),
):
    conn = await _connection(session, connection_id, workspace)
    adapter = _adapter(conn.provider)
    names = {r["id"]: r["name"] for r in await _run(adapter.list_resources, _creds(conn), conn.config)}

    existing = await session.execute(
        select(ConnectorResource).where(ConnectorResource.connection_id == conn.id)
    )
    by_id = {r.resource_id: r for r in existing.scalars().all()}
    for resource_id in payload.resource_ids:
        row = by_id.get(resource_id)
        if row:
            row.selected = True
        else:
            row = ConnectorResource(
                connection_id=conn.id,
                workspace_id=workspace.id,
                resource_id=resource_id,
                name=names.get(resource_id, resource_id),
            )
        session.add(row)
    for resource_id, row in by_id.items():
        if resource_id not in payload.resource_ids:
            row.selected = False
            session.add(row)
    await session.commit()

    from app.tasks.connector_ingest import sync_connector

    sync_connector.apply_async(args=[conn.id])
    return {"selected": payload.resource_ids, "syncing": True}


@router.post("/{connection_id}/sync")
async def resync(
    connection_id: str,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(require_role(WorkspaceRole.MEMBER)),
):
    conn = await _connection(session, connection_id, workspace)
    from app.tasks.connector_ingest import sync_connector

    sync_connector.apply_async(args=[conn.id])
    return {"syncing": True}
