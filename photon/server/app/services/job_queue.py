"""
job_queue.py — Celery task dispatch helpers.

Re-exports the Celery app and provides a thin wrapper so routers never
import Celery internals directly.
"""
from __future__ import annotations

import structlog
from app.tasks.celery_app import celery_app  # noqa: F401  (re-export)
from app.tasks.ingestion import run_ingestion

log = structlog.get_logger()


def dispatch_ingestion(repo_id: str, job_id: str) -> str:
    """
    Dispatch the ingestion Celery task and return the Celery task ID.

    Args:
        repo_id: UUID of the Repo row.
        job_id:  UUID of the Job row (also used as the Celery task ID).

    Returns:
        The Celery task ID string.
    """
    result = run_ingestion.apply_async(
        args=[repo_id, job_id],
        task_id=job_id,
    )
    log.info("ingestion.dispatched", repo_id=repo_id, job_id=job_id, celery_id=result.id)
    return result.id
