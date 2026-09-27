"""Celery task: sync a Jira connection's selected projects."""
from __future__ import annotations

from datetime import datetime

import structlog
from sqlmodel import Session, select

from app.core.crypto import decrypt
from app.database import get_sync_engine
from app.models import JiraConnection, JiraProject
from app.services.jira_sync import sync_project
from app.tasks.celery_app import celery_app

log = structlog.get_logger()


@celery_app.task(bind=True, name="app.tasks.jira_ingest.sync_jira")
def sync_jira(self, connection_id: str) -> dict:
    engine = get_sync_engine()
    with Session(engine) as session:
        conn = session.get(JiraConnection, connection_id)
        if not conn:
            return {"error": "connection not found"}
        token = decrypt(conn.api_token_encrypted)
        if not token:
            log.error("jira_ingest.token_undecryptable", connection_id=connection_id)
            return {"error": "token could not be decrypted — reconnect Jira"}
        site, email, workspace_id = conn.site_url, conn.account_email, conn.workspace_id
        targets = [
            (p.id, p.project_key, p.last_synced_at)
            for p in session.exec(
                select(JiraProject).where(
                    JiraProject.connection_id == connection_id,
                    JiraProject.selected == True,  # noqa: E712
                )
            ).all()
        ]

    total = 0
    for row_id, key, last_synced in targets:
        try:
            # Jira's JQL date format; minute precision is plenty and avoids
            # re-pulling the whole project every sync.
            since = last_synced.strftime("%Y-%m-%d %H:%M") if last_synced else None
            count = sync_project(site, email, token, workspace_id, key, since)
        except Exception as exc:  # noqa: BLE001 - one project must not sink the sync
            log.error("jira_ingest.project_failed", project=key, error=str(exc))
            continue
        total += count
        with Session(engine) as session:
            row = session.get(JiraProject, row_id)
            if row:
                row.issue_count += count
                row.last_synced_at = datetime.utcnow()
                session.add(row)
                session.commit()

    with Session(engine) as session:
        conn = session.get(JiraConnection, connection_id)
        if conn:
            conn.last_synced_at = datetime.utcnow()
            session.add(conn)
            session.commit()

    log.info("jira_ingest.done", connection_id=connection_id, issues=total)
    return {"issues": total, "projects": len(targets)}
