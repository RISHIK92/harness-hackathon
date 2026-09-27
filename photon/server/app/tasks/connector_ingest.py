"""Celery task: sync one external connection's selected resources."""
from __future__ import annotations

import json
from datetime import datetime

import structlog
from sqlmodel import Session, select

from app.core.crypto import decrypt
from app.database import get_sync_engine
from app.models import ConnectorResource, ExternalConnection
from app.services.connectors.base import index_items
from app.tasks.celery_app import celery_app

log = structlog.get_logger()


@celery_app.task(bind=True, name="app.tasks.connector_ingest.sync_connector")
def sync_connector(self, connection_id: str) -> dict:
    from app.routers.connectors import ADAPTERS  # imported late: avoids a cycle at module load

    engine = get_sync_engine()
    with Session(engine) as session:
        conn = session.get(ExternalConnection, connection_id)
        if not conn:
            return {"error": "connection not found"}
        raw = decrypt(conn.credentials_encrypted)
        if not raw:
            log.error("connector_ingest.undecryptable", connection_id=connection_id)
            return {"error": "credentials could not be decrypted — reconnect"}
        credentials, config = json.loads(raw), conn.config or {}
        provider, workspace_id = conn.provider, conn.workspace_id
        targets = [
            (r.id, r.resource_id)
            for r in session.exec(
                select(ConnectorResource).where(
                    ConnectorResource.connection_id == connection_id,
                    ConnectorResource.selected == True,  # noqa: E712
                )
            ).all()
        ]

    adapter = ADAPTERS[provider]
    total = 0
    for row_id, resource_id in targets:
        try:
            items = adapter.fetch(credentials, config, resource_id)
            count = index_items(workspace_id, provider.value, resource_id, items)
        except Exception as exc:  # noqa: BLE001 - one resource must not sink the sync
            log.error("connector_ingest.resource_failed", provider=provider.value,
                      resource=resource_id, error=str(exc))
            continue
        total += count
        with Session(engine) as session:
            row = session.get(ConnectorResource, row_id)
            if row:
                row.item_count = count
                row.last_synced_at = datetime.utcnow()
                session.add(row)
                session.commit()

    with Session(engine) as session:
        conn = session.get(ExternalConnection, connection_id)
        if conn:
            conn.last_synced_at = datetime.utcnow()
            session.add(conn)
            session.commit()

    log.info("connector_ingest.done", provider=provider.value, items=total)
    return {"items": total, "resources": len(targets)}
