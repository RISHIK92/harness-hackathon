from __future__ import annotations
import os
import re
import zipfile
import io
import asyncio
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Header, Query
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import SQLModel, select

from app.database import get_session
from app.models import WorkspaceRole, Repo, RepoCreate, RepoRead, RepoStatus, RepoSourceType, Job, User, Workspace
from app.config import get_settings
from app.tasks.ingestion import run_ingestion
from app.core.auth import get_current_user
from app.core.workspace import get_current_workspace, require_role
from app.core.repo_access import may_access
from app.services.estimate import estimate as compute_estimate, files_from_size_kb

router = APIRouter()
settings = get_settings()


class EstimateRequest(SQLModel):
    """Either exact file counts (if we already know them) or GitHub's
    repo size in KB (all we have before cloning)."""
    file_counts: list[int] = []
    size_kb: list[int] = []


@router.post("/estimate")
async def estimate_ingest_time(
    payload: EstimateRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """How long importing these repos will take, as a RANGE.

    Calibrated from ingests already completed on this deployment, so the
    number reflects this machine, this embedding provider and this network
    rather than a constant written once. Samples are drawn across all
    repos on purpose: this measures the pipeline's speed, not any one
    tenant's data, and only the fitted range is returned — never another
    workspace's rows.
    """
    result = await session.execute(
        select(Repo.file_count, Repo.ingest_seconds).where(
            Repo.ingest_seconds.is_not(None), Repo.file_count > 0
        )
    )
    samples = [(int(f), float(sec)) for f, sec in result.all()]

    counts = list(payload.file_counts) + [files_from_size_kb(kb) for kb in payload.size_kb]
    est = compute_estimate(counts, samples)
    return {
        "range_human": est.human,
        "seconds_low": est.seconds_low,
        "seconds_high": est.seconds_high,
        "repo_count": est.repo_count,
        "file_count_estimated": est.file_count,
        # Surfaced so the UI can say "estimated from N previous imports"
        # instead of implying more confidence than we have.
        "calibrated": est.calibrated,
        "sample_size": est.sample_size,
    }


# Moved to app/core/repo_access.py so the legacy repo-scoped routers share it.
_may_access = may_access

_GITHUB_URL = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?(\.git)?/?$")


def _validate_source(payload: RepoCreate) -> None:
    if payload.source_type == RepoSourceType.GITHUB:
        if not payload.source_url or not _GITHUB_URL.match(payload.source_url.strip()):
            raise HTTPException(status_code=422, detail="Enter a GitHub repository URL like https://github.com/owner/repo")
        payload.source_url = payload.source_url.strip()
        return
    if payload.source_type == RepoSourceType.LOCAL and settings.app_env == "development":
        return
    raise HTTPException(status_code=422, detail="Repositories are connected from GitHub (or uploaded as a zip)")


@router.post("", response_model=RepoRead, status_code=201)
async def create_repo(
    payload: RepoCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(require_role(WorkspaceRole.MEMBER)),
):
    """Connect a repository (a public GitHub URL; a local path in development).

    The source is validated: it used to be any string. A local path was
    symlinked from anywhere on the server into storage and served back
    through the file routes, and a clone URL on any host received the
    deployment's GitHub token."""
    _validate_source(payload)
    repo = Repo(**payload.model_dump(), owner_id=current_user.id, workspace_id=workspace.id)
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    # Create job record
    job = Job(repo_id=repo.id)
    session.add(job)
    await session.commit()
    await session.refresh(job)

    # Dispatch Celery ingestion task
    run_ingestion.apply_async(
        args=[repo.id, job.id],
        task_id=job.id,
    )

    return repo


@router.post("/upload", response_model=RepoRead, status_code=201)
async def upload_zip(
    name: str = Form(...),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Upload a ZIP archive for ingestion."""
    storage = settings.repos_storage_path

    # Save zip metadata
    repo = Repo(name=name, source_type=RepoSourceType.ZIP, owner_id=current_user.id)
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    repo_dir = os.path.join(storage, repo.id)
    os.makedirs(repo_dir, exist_ok=True)
    contents = await file.read()
    
    # Run CPU-bound extraction in a thread to avoid blocking the async event loop
    def extract_zip():
        with zipfile.ZipFile(io.BytesIO(contents)) as z:
            z.extractall(repo_dir)
            
    await asyncio.to_thread(extract_zip)

    repo.local_path = repo_dir
    session.add(repo)
    await session.commit()

    job = Job(repo_id=repo.id)
    session.add(job)
    await session.commit()
    await session.refresh(job)

    run_ingestion.apply_async(args=[repo.id, job.id], task_id=job.id)
    return repo


@router.get("", response_model=list[RepoRead])
async def list_repos(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
):
    # Workspace-scoped, not user-scoped: a teammate joining a workspace
    # must see its repos, and the same repo must NOT leak across workspaces.
    result = await session.execute(
        select(Repo).where(Repo.workspace_id == workspace.id).order_by(Repo.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{repo_id}", response_model=RepoRead)
async def get_repo(
    repo_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
):
    repo = await session.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    if not _may_access(repo, current_user, workspace):
        raise HTTPException(status_code=404, detail="Repo not found")
    return repo


@router.get("/{repo_id}/file")
async def read_repo_file(
    repo_id: str,
    path: str = Query(..., min_length=1, max_length=1024),
    start: Optional[int] = Query(default=None, ge=1),
    end: Optional[int] = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
):
    """Lines `start..end` of one file, for the call's code panel.

    Replaces the panel's use of the raw, unauthenticated tool endpoint: the
    same read, behind the same workspace check as every other repo route.
    Returns the read_file tool envelope, which is what the panel parses."""
    repo = await session.get(Repo, repo_id)
    if not repo or not _may_access(repo, current_user, workspace):
        raise HTTPException(status_code=404, detail="Repo not found")
    from app.tools.code import read_file

    return await read_file(path, repo_id=repo.id, start=start, end=end)


@router.delete("/{repo_id}", status_code=204)
async def delete_repo(
    repo_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(require_role(WorkspaceRole.MEMBER)),
):
    repo = await session.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    if not _may_access(repo, current_user, workspace):
        raise HTTPException(status_code=404, detail="Repo not found")
    await session.delete(repo)
    await session.commit()
