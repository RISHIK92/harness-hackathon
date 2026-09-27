from __future__ import annotations
import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.database import get_session
from app.models import Job, JobRead
from app.config import get_settings
import redis.asyncio as aioredis

router = APIRouter()
settings = get_settings()


@router.get("/repo/{repo_id}", response_model=list[JobRead])
async def list_jobs_for_repo(repo_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.exec(
        select(Job).where(Job.repo_id == repo_id).order_by(Job.created_at.desc())
    )
    return result.all()


@router.get("/{job_id}/stream")
async def stream_job_progress(job_id: str, session: AsyncSession = Depends(get_session)):
    """SSE stream of job progress events."""
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = r.pubsub()
        await pubsub.subscribe(f"job:{job_id}")
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    yield f"data: {message['data']}\n\n"
                    data = json.loads(message["data"])
                    if data.get("phase") in ("done", "failed"):
                        break
        finally:
            await pubsub.unsubscribe(f"job:{job_id}")
            await r.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{job_id}", response_model=JobRead)
async def get_job(job_id: str, session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
