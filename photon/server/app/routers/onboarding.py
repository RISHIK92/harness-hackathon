"""onboarding.py — Learning path / onboarding router."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import LearningPathCache, Repo
from app.services.learning_path import generate_learning_path

router = APIRouter()


@router.get("/{repo_id}/learning-path")
async def get_learning_path(
    repo_id: str,
    regenerate: bool = Query(default=False, description="Force regeneration even if a cached version exists"),
    session: AsyncSession = Depends(get_session),
):
    """
    Return a sequenced onboarding plan for a repository.

    - First call (or `?regenerate=true`): runs the full pipeline (graph traversal
      + Gemini summarisation) and persists the result to the DB.
    - Subsequent calls: returns the cached result instantly.
    """
    repo = await session.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    if repo.status.value != "READY":
        raise HTTPException(
            status_code=409,
            detail="Repo not yet ready — indexing may still be running",
        )

    # ── Cache lookup ──────────────────────────────────────────────────────────
    if not regenerate:
        result = await session.exec(
            select(LearningPathCache).where(LearningPathCache.repo_id == repo_id)
        )
        cached = result.first()
        if cached:
            return {**cached.data, "cached": True, "generated_at": cached.generated_at.isoformat()}

    # ── Generate ──────────────────────────────────────────────────────────────
    path = await generate_learning_path(repo_id)

    # ── Upsert into DB ────────────────────────────────────────────────────────
    result = await session.exec(
        select(LearningPathCache).where(LearningPathCache.repo_id == repo_id)
    )
    existing = result.first()
    if existing:
        existing.data = path
        existing.generated_at = datetime.utcnow()
        session.add(existing)
    else:
        session.add(LearningPathCache(repo_id=repo_id, data=path))

    await session.commit()

    return {**path, "cached": False}


@router.delete("/{repo_id}/learning-path")
async def delete_learning_path(
    repo_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Delete the cached learning path so the next GET regenerates it."""
    result = await session.exec(
        select(LearningPathCache).where(LearningPathCache.repo_id == repo_id)
    )
    cached = result.first()
    if cached:
        await session.delete(cached)
        await session.commit()
    return {"deleted": True}
