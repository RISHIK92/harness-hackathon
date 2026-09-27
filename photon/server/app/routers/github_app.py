"""GitHub App installation ("Connect GitHub", per workspace) and the repo
picker for choosing which of an installation's repos to actually import.

The install-initiation step needs two things a plain `<a href>` redirect
can't give us: (1) proof of who's asking (normal bearer-token auth), and
(2) which workspace this installation should belong to. So it's a POST
(authenticated exactly like every other API call) that hands back a URL
for the client to navigate to — never a token embedded in a URL that's
about to redirect to github.com (a Referer-header leak risk). The nonce
it stashes in Redis, passed to GitHub as the `state` param, is how the
callback (a plain top-level GET redirect, unauthenticated by construction
— GitHub is the one hitting it) recovers which workspace to bind the
installation to.
"""
from __future__ import annotations

import secrets

import httpx
import redis
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.core.auth import get_current_user
from app.core.workspace import get_current_workspace, require_role
from app.database import get_session
from app.models import Repo, RepoStatus, WorkspaceRole, GitHubInstallation, GitHubInstallationRead, Job, Repo, RepoSourceType, User, Workspace
from app.services.github_app_auth import generate_app_jwt, get_installation_token_async
from app.tasks.ingestion import run_ingestion

log = structlog.get_logger()
router = APIRouter()
settings = get_settings()

_INSTALL_NONCE_TTL_SECONDS = 600
_redis_client: "redis.Redis | None" = None


def _redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.redis_url)
    return _redis_client


async def _get_installation_for_workspace(session: AsyncSession, installation_id: int, workspace_id: str) -> GitHubInstallation:
    result = await session.execute(select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id))
    install = result.scalar_one_or_none()
    # 404, not 403, on a mismatch — same tenancy rule as everywhere else
    # (core/workspace.py): a non-member should not learn that an
    # installation id exists at all.
    if not install or install.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="GitHub installation not found")
    return install


# ─── Install ─────────────────────────────────────────────────────────────────

@router.get("/app")
async def app_info(current_user: User = Depends(get_current_user)):
    """What this deployment's GitHub App actually is, straight from GitHub.

    Exists so the pre-flight dialog can tell the user something specific
    ("this App is owned by @you and cannot be installed on an org yet")
    instead of generic documentation they have to map onto their own
    situation. Read-only and cheap: one call, and the answer only changes
    when an operator edits the App.
    """
    if not settings.github_app_id or not settings.github_app_private_key:
        raise HTTPException(status_code=503, detail="GitHub App is not configured on this deployment")

    jwt_token = generate_app_jwt()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            "https://api.github.com/app",
            headers={"Authorization": f"Bearer {jwt_token}", "Accept": "application/vnd.github+json"},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Could not reach GitHub to check the app configuration")
    data = resp.json()
    owner = data.get("owner") or {}

    # GitHub's /app response does not reliably carry the `public` flag, so
    # org-installability is derived from ownership, which it always sends:
    # an App owned by a USER can only be installed on that user's account
    # unless it is made public or transferred to the org.
    owner_type = owner.get("type", "User")
    return {
        "slug": data.get("slug"),
        "name": data.get("name"),
        "owner_login": owner.get("login"),
        "owner_type": owner_type,
        "permissions": data.get("permissions") or {},
        "installations_count": data.get("installations_count", 0),
        "org_install_supported": owner_type == "Organization",
        "settings_url": f"https://github.com/settings/apps/{data.get('slug')}",
    }


@router.get("/status")
async def github_status(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
):
    """One place that answers "is GitHub connected?".

    GitHub is genuinely two things — an IDENTITY (this account is
    @someone) and ACCESS (the app is installed on an account or org, which
    is what can read code). They were exposed separately, so the console
    showed "connect" beside a GitHub already signed in with, and a user who
    had done half the flow could not tell which half.

    Identity is per USER and survives switching workspace; access is per
    WORKSPACE, because that is where the repos land. This returns both, so
    every surface can render the same state instead of each deciding.
    """
    result = await session.execute(
        select(GitHubInstallation).where(GitHubInstallation.workspace_id == workspace.id)
    )
    installations = result.scalars().all()

    ready_repos = (await session.execute(
        select(Repo).where(Repo.workspace_id == workspace.id, Repo.status == RepoStatus.READY)
    )).scalars().all()

    identity_linked = bool(current_user.github_id)
    return {
        "identity": {
            "linked": identity_linked,
            "login": current_user.github_login,
        },
        "access": {
            "installations": [
                {"id": i.installation_id, "account": i.account_login, "type": i.account_type}
                for i in installations
            ],
            "repos_indexed": len(ready_repos),
        },
        # What the UI should offer next, decided here rather than in three
        # places that could disagree.
        "state": (
            "ready" if ready_repos
            else "installed_no_repos" if installations
            else "identity_only" if identity_linked
            else "not_connected"
        ),
        "app_configured": bool(settings.github_app_slug and settings.github_app_private_key),
    }


@router.post("/connect")
async def start_install(
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(require_role(WorkspaceRole.OWNER)),
):
    if not settings.github_app_slug:
        raise HTTPException(status_code=503, detail="GitHub App is not configured on this deployment")
    nonce = secrets.token_urlsafe(24)
    _redis().set(f"gh:install_nonce:{nonce}", workspace.id, ex=_INSTALL_NONCE_TTL_SECONDS)
    return {"url": f"https://github.com/apps/{settings.github_app_slug}/installations/new?state={nonce}"}


@router.get("/callback")
async def install_callback(
    installation_id: int = Query(...),
    setup_action: str = Query(default=""),
    state: str = Query(default=""),
    session: AsyncSession = Depends(get_session),
):
    raw = _redis().get(f"gh:install_nonce:{state}") if state else None
    if not raw:
        raise HTTPException(status_code=400, detail="Expired or invalid installation attempt — please try Connect GitHub again")
    workspace_id = raw.decode() if isinstance(raw, bytes) else raw
    _redis().delete(f"gh:install_nonce:{state}")  # single-use

    # Fetch the account (org/user) this installation belongs to, for a
    # human-readable label — GitHub's redirect only gives us the numeric id.
    app_jwt = generate_app_jwt()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"https://api.github.com/app/installations/{installation_id}",
            headers={"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        account = resp.json().get("account", {})

    result = await session.execute(select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id))
    row = result.scalar_one_or_none()
    if row:
        row.workspace_id = workspace_id
        row.account_login = account.get("login", row.account_login)
        row.account_type = account.get("type", row.account_type)
    else:
        row = GitHubInstallation(
            workspace_id=workspace_id,
            installation_id=installation_id,
            account_login=account.get("login", "unknown"),
            account_type=account.get("type", "unknown"),
        )
    session.add(row)
    await session.commit()

    log.info("github_app.installation_connected", installation_id=installation_id, workspace_id=workspace_id, setup_action=setup_action)
    return RedirectResponse(f"{settings.client_base_url}/dashboard?installation=connected")


@router.get("", response_model=list[GitHubInstallationRead])
async def list_installations(
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(GitHubInstallation).where(GitHubInstallation.workspace_id == workspace.id))
    return result.scalars().all()


# ─── Repo picker ────────────────────────────────────────────────────────────

@router.get("/{installation_id}/repos")
async def list_installation_repos(
    installation_id: int,
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
):
    """Repos visible to this installation right now, tagged with whether
    they're already imported — computed live on every call rather than
    cached, so reopening the picker after the org admin grants access to
    more repos always shows the true current delta."""
    install = await _get_installation_for_workspace(session, installation_id, workspace.id)
    token = await get_installation_token_async(installation_id)

    repos: list[dict] = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        page = 1
        while True:
            resp = await client.get(
                "https://api.github.com/installation/repositories",
                headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json().get("repositories", [])
            repos.extend(batch)
            if len(batch) < 100:
                break
            page += 1

    imported = await session.execute(
        select(Repo.github_repo_id).where(
            Repo.workspace_id == workspace.id,
            Repo.github_installation_id == installation_id,
        )
    )
    imported_ids = {r for (r,) in imported.all() if r is not None}

    items = [
        {
            "id": r["id"],
            "full_name": r["full_name"],
            "private": r["private"],
            "clone_url": r["clone_url"],
            # GitHub reports size in KB. Carried through so the picker can
            # show an ingest-time estimate BEFORE anything is cloned —
            # file counts don't exist yet at this point (see
            # services/estimate.py:files_from_size_kb).
            "size_kb": r.get("size") or 0,
            "already_imported": r["id"] in imported_ids,
        }
        for r in repos
    ]
    items.sort(key=lambda x: x["already_imported"])  # new repos first
    return {"installation": GitHubInstallationRead.model_validate(install), "repos": items}


class ImportRepoItem(SQLModel):
    id: int
    full_name: str
    clone_url: str


class ImportReposRequest(SQLModel):
    repos: list[ImportRepoItem]


@router.post("/{installation_id}/repos/import")
async def import_installation_repos(
    installation_id: int,
    payload: ImportReposRequest,
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(require_role(WorkspaceRole.MEMBER)),
    session: AsyncSession = Depends(get_session),
):
    """Create a Repo + dispatch ingestion for each selected repo not
    already imported — mirrors routers/repos.py::create_repo exactly,
    just sourced from the installation's repo list instead of a
    hand-typed URL, and tagged with github_repo_id/github_installation_id
    so the picker can compute the diff on the next visit."""
    await _get_installation_for_workspace(session, installation_id, workspace.id)

    created: list[Repo] = []
    for item in payload.repos:
        existing = await session.execute(
            select(Repo).where(Repo.workspace_id == workspace.id, Repo.github_repo_id == item.id)
        )
        if existing.scalar_one_or_none():
            continue  # already imported — never re-create or re-ingest

        repo = Repo(
            name=item.full_name,
            source_type=RepoSourceType.GITHUB,
            source_url=item.clone_url,
            workspace_id=workspace.id,
            owner_id=current_user.id,
            github_repo_id=item.id,
            github_installation_id=installation_id,
        )
        session.add(repo)
        await session.commit()
        await session.refresh(repo)

        job = Job(repo_id=repo.id)
        session.add(job)
        await session.commit()
        await session.refresh(job)

        run_ingestion.apply_async(args=[repo.id, job.id], task_id=job.id)
        created.append(repo)

    return created
