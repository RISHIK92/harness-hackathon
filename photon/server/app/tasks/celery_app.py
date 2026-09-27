from celery import Celery
from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "yasml",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    imports=["app.tasks.ingestion", "app.tasks.slack_ingest", "app.tasks.jira_ingest", "app.tasks.connector_ingest", "app.tasks.agent_jobs"],
)
