"""Celery task: sync a workspace's selected Slack channels."""
from __future__ import annotations

from datetime import datetime

import structlog
from sqlmodel import Session, select

from app.core.crypto import decrypt
from app.database import get_sync_engine
from app.models import SlackChannel, SlackInstallation
from app.services.slack_sync import sync_channel
from app.tasks.celery_app import celery_app

log = structlog.get_logger()


@celery_app.task(bind=True, name="app.tasks.slack_ingest.sync_slack")
def sync_slack(self, installation_id: str) -> dict:
    engine = get_sync_engine()
    with Session(engine) as session:
        install = session.get(SlackInstallation, installation_id)
        if not install:
            return {"error": "installation not found"}
        token = decrypt(install.bot_token_encrypted)
        if not token:
            # secret_key rotated: fail loudly rather than syncing nothing
            # and reporting success.
            log.error("slack_ingest.token_undecryptable", installation_id=installation_id)
            return {"error": "token could not be decrypted — reconnect Slack"}

        channels = session.exec(
            select(SlackChannel).where(
                SlackChannel.installation_id == installation_id,
                SlackChannel.selected == True,  # noqa: E712
            )
        ).all()
        workspace_id = install.workspace_id
        targets = [(c.id, c.channel_id, c.name, c.last_synced_at) for c in channels]

    total = 0
    for row_id, channel_id, name, last_synced in targets:
        try:
            # Incremental: only messages since the last sync. A full re-pull
            # of a busy channel is slow AND re-embeds text that has not
            # changed, which costs real money.
            oldest = last_synced.timestamp() if last_synced else None
            count = sync_channel(token, workspace_id, channel_id, name, oldest=oldest)
        except Exception as exc:  # noqa: BLE001 - one bad channel must not sink the sync
            log.error("slack_ingest.channel_failed", channel=name, error=str(exc))
            continue
        total += count
        with Session(engine) as session:
            row = session.get(SlackChannel, row_id)
            if row:
                row.message_count += count
                row.last_synced_at = datetime.utcnow()
                session.add(row)
                session.commit()

    with Session(engine) as session:
        install = session.get(SlackInstallation, installation_id)
        if install:
            install.last_synced_at = datetime.utcnow()
            session.add(install)
            session.commit()

    log.info("slack_ingest.done", installation_id=installation_id, messages=total)
    return {"messages": total, "channels": len(targets)}
