"""Agent jobs — a member's agent fixing tickets, with the member in charge.

    POST /api/agent-jobs                 hand a ticket to your agent
    GET  /api/agent-jobs                 your jobs (a workspace owner sees all)
    GET  /api/agent-jobs/harness         is the harness service reachable
    GET  /api/agent-jobs/{id}
    POST /api/agent-jobs/{id}/approve    {files?, constraints?} — approve or narrow
    POST /api/agent-jobs/{id}/reject     {reason?}
    POST /api/agent-jobs/{id}/replan

    POST /api/integrations/github/webhook   issues + issue_comment events
    POST /api/integrations/linear/webhook   Issue + Comment events

A ticket labelled for the agent goes to the assignee's agent. Unassigned,
it goes to the org agent — only where an owner has enabled it.

A job belongs to one member, its point of contact. Only that member — or a
workspace owner, who answers for the workspace — can decide on its plan.
Anyone else gets a 404, the same rule as a whisper thread: an id is never
confirmed to exist to someone who could not open it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.core.auth import get_current_user
from app.core.workspace import get_current_workspace, membership_for, role_at_least
from app.database import get_session
from app.models import (
    ExternalConnection,
    AgentJob,
    AgentJobRead,
    AgentJobSource,
    AgentJobStatus,
    Repo,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from app.services import agent_jobs as rules
from app.services import harness_client
from app.services import linear_tickets

router = APIRouter()
webhook_router = APIRouter()
linear_router = APIRouter()

OPEN = (AgentJobStatus.PLANNING.value, AgentJobStatus.AWAITING_APPROVAL.value,
        AgentJobStatus.FIXING.value)


class AgentJobCreate(SQLModel):
    repo_id: str
    title: str
    body: str = ""
    context: str = ""
    ticket_ref: Optional[str] = None
    ticket_url: Optional[str] = None
    source: AgentJobSource = AgentJobSource.MANUAL


class ApproveBody(SQLModel):
    files: Optional[list[str]] = None
    constraints: str = ""


class RejectBody(SQLModel):
    reason: str = ""


class ConfirmBody(SQLModel):
    repo_id: str
    title: Optional[str] = None
    body: Optional[str] = None


# ── shared ───────────────────────────────────────────────────────────────

def _enqueue_plan(job_id: str) -> None:
    from app.tasks.agent_jobs import plan_agent_job
    plan_agent_job.delay(job_id)


def _enqueue_fix(job_id: str) -> None:
    from app.tasks.agent_jobs import fix_agent_job
    fix_agent_job.delay(job_id)


async def _is_workspace_owner(session: AsyncSession, workspace_id: str, user_id: str) -> bool:
    member = await membership_for(session, workspace_id, user_id)
    return bool(member and role_at_least(member.role, WorkspaceRole.OWNER))


async def _job_for(session: AsyncSession, job_id: str, workspace: Workspace, user: User) -> AgentJob:
    job = await session.get(AgentJob, job_id)
    if not job or job.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.owner_user_id != user.id and not await _is_workspace_owner(session, workspace.id, user.id):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def decide(session: AsyncSession, job: AgentJob, command: rules.Command, actor_id: str) -> AgentJob:
    """One path for a decision, whether it came from the console or a ticket."""
    if job.status != AgentJobStatus.AWAITING_APPROVAL.value:
        raise HTTPException(status_code=409, detail=f"The job is {job.status}, not awaiting approval")
    if command.kind == "reject":
        job.status = AgentJobStatus.REJECTED.value
        job.escalation_reason = command.note or "rejected by the owner"
        enqueue = None
    else:
        try:
            scope = rules.approved_scope(job.plan or {},
                                         command.files if command.kind == "narrow" else None,
                                         command.note)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        job.approved_scope = scope
        job.approved_by = actor_id
        job.status = AgentJobStatus.FIXING.value
        enqueue = _enqueue_fix
    job.updated_at = datetime.utcnow()
    session.add(job)
    await session.commit()
    await session.refresh(job)
    if enqueue:
        enqueue(job.id)
    return job


# ── console ──────────────────────────────────────────────────────────────

@router.get("/harness")
async def harness_status(user: User = Depends(get_current_user)):
    return {"reachable": harness_client.health(),
            "url": get_settings().harness_service_url}


@router.post("", response_model=AgentJobRead, status_code=201)
async def create_job(
    body: AgentJobCreate,
    workspace: Workspace = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    member = await membership_for(session, workspace.id, user.id)
    if not member or not role_at_least(member.role, WorkspaceRole.MEMBER):
        raise HTTPException(status_code=403, detail="This action needs the member role in this workspace")
    repo = await session.get(Repo, body.repo_id)
    if not repo or repo.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Repo not found")
    if not repo.source_url:
        raise HTTPException(status_code=422, detail="That repository has no clone URL to fix against")
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="A ticket needs a title")
    # Always MANUAL from the console. `source` decides where plan and outcome
    # comments are posted, and a client-chosen "github" + any ticket_ref let
    # a member make Photon comment on an arbitrary owner/repo#N with the
    # installation's token. GitHub and Linear jobs are created by their
    # signed webhooks only.
    job = AgentJob(
        workspace_id=workspace.id, owner_user_id=user.id, repo_id=repo.id,
        source=AgentJobSource.MANUAL.value, ticket_ref=body.ticket_ref, ticket_url=body.ticket_url,
        title=body.title.strip(), issue_text=rules.issue_text(body.title, body.body, body.context),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    _enqueue_plan(job.id)
    return job


@router.get("", response_model=list[AgentJobRead])
async def list_jobs(
    workspace: Workspace = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    query = select(AgentJob).where(AgentJob.workspace_id == workspace.id)
    if not await _is_workspace_owner(session, workspace.id, user.id):
        query = query.where(AgentJob.owner_user_id == user.id)
    result = await session.execute(query.order_by(AgentJob.created_at.desc()))
    return result.scalars().all()


@router.get("/{job_id}", response_model=AgentJobRead)
async def get_job(job_id: str, workspace: Workspace = Depends(get_current_workspace),
                  session: AsyncSession = Depends(get_session),
                  user: User = Depends(get_current_user)):
    return await _job_for(session, job_id, workspace, user)


@router.post("/{job_id}/approve", response_model=AgentJobRead)
async def approve_job(job_id: str, body: ApproveBody,
                      workspace: Workspace = Depends(get_current_workspace),
                      session: AsyncSession = Depends(get_session),
                      user: User = Depends(get_current_user)):
    job = await _job_for(session, job_id, workspace, user)
    kind = "narrow" if body.files else "approve"
    return await decide(session, job, rules.Command(kind, files=body.files or [], note=body.constraints), user.id)


@router.post("/{job_id}/reject", response_model=AgentJobRead)
async def reject_job(job_id: str, body: RejectBody,
                     workspace: Workspace = Depends(get_current_workspace),
                     session: AsyncSession = Depends(get_session),
                     user: User = Depends(get_current_user)):
    job = await _job_for(session, job_id, workspace, user)
    return await decide(session, job, rules.Command("reject", note=body.reason), user.id)


@router.post("/{job_id}/confirm", response_model=AgentJobRead)
async def confirm_draft(job_id: str, body: ConfirmBody,
                        workspace: Workspace = Depends(get_current_workspace),
                        session: AsyncSession = Depends(get_session),
                        user: User = Depends(get_current_user)):
    """Turn a draft (a fix promised on a call) into a real job. The member
    names the repository — a sentence on a call does not — and may reword
    the ticket before the agent plans against it."""
    job = await _job_for(session, job_id, workspace, user)
    if job.status != AgentJobStatus.DRAFT.value:
        raise HTTPException(status_code=409, detail=f"The job is {job.status}, not a draft")
    repo = await session.get(Repo, body.repo_id)
    if not repo or repo.workspace_id != workspace.id or not repo.source_url:
        raise HTTPException(status_code=404, detail="Repo not found")
    job.repo_id = repo.id
    if body.title and body.title.strip():
        job.title = body.title.strip()
    if body.body and body.body.strip():
        job.issue_text = rules.issue_text(job.title, body.body)
    job.status = AgentJobStatus.PLANNING.value
    job.updated_at = datetime.utcnow()
    session.add(job)
    await session.commit()
    await session.refresh(job)
    _enqueue_plan(job.id)
    return job


@router.post("/{job_id}/discard", response_model=AgentJobRead)
async def discard_draft(job_id: str, workspace: Workspace = Depends(get_current_workspace),
                        session: AsyncSession = Depends(get_session),
                        user: User = Depends(get_current_user)):
    job = await _job_for(session, job_id, workspace, user)
    if job.status != AgentJobStatus.DRAFT.value:
        raise HTTPException(status_code=409, detail=f"The job is {job.status}, not a draft")
    job.status = AgentJobStatus.REJECTED.value
    job.escalation_reason = "discarded draft"
    job.updated_at = datetime.utcnow()
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


@router.post("/{job_id}/replan", response_model=AgentJobRead)
async def replan_job(job_id: str, workspace: Workspace = Depends(get_current_workspace),
                     session: AsyncSession = Depends(get_session),
                     user: User = Depends(get_current_user)):
    job = await _job_for(session, job_id, workspace, user)
    if job.status in OPEN or job.status == AgentJobStatus.DRAFT.value:
        raise HTTPException(status_code=409, detail=f"The job is still {job.status}")
    job.status = AgentJobStatus.PLANNING.value
    job.plan = job.approved_scope = job.result = None
    job.plan_run_id = job.fix_run_id = job.pr_url = job.escalation_reason = None
    job.updated_at = datetime.utcnow()
    session.add(job)
    await session.commit()
    await session.refresh(job)
    _enqueue_plan(job.id)
    return job


# ── GitHub webhook ───────────────────────────────────────────────────────

def signature_ok(secret: str, body: bytes, header: Optional[str]) -> bool:
    if not secret or not header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


async def _member_by_login(session: AsyncSession, workspace_id: str, login: str) -> Optional[User]:
    if not login:
        return None
    result = await session.execute(
        select(User).join(WorkspaceMember, WorkspaceMember.user_id == User.id).where(
            WorkspaceMember.workspace_id == workspace_id,
            User.github_login == login,
        )
    )
    return result.scalars().first()


@webhook_router.post("/webhook")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: Optional[str] = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    secret = get_settings().github_app_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="GITHUB_APP_WEBHOOK_SECRET is not configured")
    raw = await request.body()
    if not signature_ok(secret, raw, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="bad signature")
    event = json.loads(raw or b"{}")
    if x_github_event == "issues":
        return await _on_issue(session, event)
    if x_github_event == "issue_comment":
        return await _on_comment(session, event)
    return {"ignored": x_github_event}


async def _repos_for(session: AsyncSession, event: dict) -> list[Repo]:
    repo_id = (event.get("repository") or {}).get("id")
    if not repo_id:
        return []
    result = await session.execute(select(Repo).where(Repo.github_repo_id == repo_id))
    return list(result.scalars().all())


async def _on_issue(session: AsyncSession, event: dict) -> dict:
    action = event.get("action")
    issue = event.get("issue") or {}
    if issue.get("pull_request") or action not in ("assigned", "labeled"):
        return {"ignored": action}
    label = get_settings().agent_fix_label
    if action == "labeled" and (event.get("label") or {}).get("name") != label:
        return {"ignored": "label"}
    if action == "assigned" and label not in [l.get("name") for l in issue.get("labels") or []]:
        # Assignment alone hands the ticket to a person. The label is what
        # says their agent may take it; otherwise every assignment in the
        # org would start a model run.
        return {"ignored": "not labelled for the agent"}
    login = ((event.get("assignee") or {}).get("login")
             or next((a.get("login") for a in issue.get("assignees") or []), None))
    ref = f"{(event.get('repository') or {}).get('full_name')}#{issue.get('number')}"
    created = []
    for repo in await _repos_for(session, event):
        owner, acting_for = await _owner_for(session, repo.workspace_id, login, by="login")
        if not owner or await _has_open_job(session, repo.workspace_id, ref):
            continue
        job = await _open_job(session, AgentJob(
            workspace_id=repo.workspace_id, owner_user_id=owner.id, repo_id=repo.id,
            source=AgentJobSource.GITHUB.value, ticket_ref=ref, ticket_url=issue.get("html_url"),
            title=(issue.get("title") or "").strip() or ref, acting_for=acting_for,
            issue_text=rules.issue_text(issue.get("title") or "", issue.get("body") or ""),
        ))
        created.append(job.id)
    return {"created": created}


async def _workspace_owner(session: AsyncSession, workspace_id: str) -> Optional[User]:
    result = await session.execute(
        select(User).join(WorkspaceMember, WorkspaceMember.user_id == User.id).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.role == WorkspaceRole.OWNER,
        ).order_by(WorkspaceMember.created_at))
    return result.scalars().first()


async def _owner_for(session: AsyncSession, workspace_id: str, who: Optional[str],
                     by: str) -> tuple[Optional[User], str]:
    """Whose agent takes this ticket.

    Assigned to a member: that member's agent. Assigned to someone who is
    not a member: nobody's — we do not act on work given to an outsider.
    Unassigned: the org agent, but only where an owner has switched it on,
    with that owner answering for the plan.
    """
    if who:
        if by == "login":
            member = await _member_by_login(session, workspace_id, who)
        else:
            member = (await session.execute(
                select(User).join(WorkspaceMember, WorkspaceMember.user_id == User.id).where(
                    WorkspaceMember.workspace_id == workspace_id, User.email == who)
            )).scalars().first()
        return member, "member"
    workspace = await session.get(Workspace, workspace_id)
    if workspace and workspace.org_agent_enabled:
        return await _workspace_owner(session, workspace_id), "company"
    return None, "member"


async def _has_open_job(session: AsyncSession, workspace_id: str, ref: str) -> bool:
    existing = await session.execute(select(AgentJob).where(
        AgentJob.workspace_id == workspace_id, AgentJob.ticket_ref == ref,
        AgentJob.status.in_(OPEN + (AgentJobStatus.DRAFT.value,))))
    return existing.scalars().first() is not None


async def _open_job(session: AsyncSession, job: AgentJob) -> AgentJob:
    """Plan straight away when the repository is known; otherwise park it as
    a draft for the owner to point at one."""
    if not job.repo_id:
        job.status = AgentJobStatus.DRAFT.value
    session.add(job)
    await session.commit()
    await session.refresh(job)
    if job.status == AgentJobStatus.PLANNING.value:
        _enqueue_plan(job.id)
    return job


async def _only_repo(session: AsyncSession, workspace_id: str) -> Optional[Repo]:
    """A Linear ticket names no repository. When the workspace has exactly
    one fixable repo, that is the answer; with several, guessing would plan
    against the wrong code, so the job waits as a draft instead."""
    from app.models import RepoStatus
    repos = (await session.execute(select(Repo).where(
        Repo.workspace_id == workspace_id, Repo.status == RepoStatus.READY,
        Repo.source_url.is_not(None), Repo.is_mock == False))).scalars().all()  # noqa: E712
    return repos[0] if len(repos) == 1 else None


# ── Linear webhook ───────────────────────────────────────────────────────

async def _linear_connections(session: AsyncSession) -> list[tuple[ExternalConnection, dict]]:
    from app.core.crypto import decrypt
    from app.models import ConnectorProvider
    rows = (await session.execute(select(ExternalConnection).where(
        ExternalConnection.provider == ConnectorProvider.LINEAR))).scalars().all()
    out = []
    for conn in rows:
        raw = decrypt(conn.credentials_encrypted)
        if raw:
            out.append((conn, json.loads(raw)))
    return out


@linear_router.post("/webhook")
async def linear_webhook(
    request: Request,
    linear_signature: Optional[str] = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    secret = get_settings().linear_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="LINEAR_WEBHOOK_SECRET is not configured")
    raw = await request.body()
    if not linear_tickets.signature_ok(secret, raw, linear_signature):
        raise HTTPException(status_code=401, detail="bad signature")
    event = json.loads(raw or b"{}")
    kind, action, data = event.get("type"), event.get("action"), event.get("data") or {}
    if kind == "Issue" and action in ("create", "update"):
        return await _on_linear_issue(session, data.get("id"))
    if kind == "Comment" and action == "create":
        return await _on_linear_comment(session, data.get("id"))
    return {"ignored": f"{kind}.{action}"}


async def _on_linear_issue(session: AsyncSession, issue_id: Optional[str]) -> dict:
    """Every connection that can SEE the issue is a workspace entitled to act
    on it — the key's own access is the boundary, same as for search."""
    if not issue_id:
        return {"ignored": "no issue id"}
    label = get_settings().agent_fix_label
    created = []
    for conn, creds in await _linear_connections(session):
        issue = linear_tickets.fetch_issue(creds, issue_id)
        if not issue or label not in linear_tickets.labels_of(issue):
            continue
        ref = issue.get("identifier") or issue_id
        email = (issue.get("assignee") or {}).get("email")
        owner, acting_for = await _owner_for(session, conn.workspace_id, email, by="email")
        # A member-scoped connection acts only for its own owner: someone's
        # personal key is not a way into tickets assigned to a colleague.
        if conn.owner_user_id and owner and owner.id != conn.owner_user_id:
            continue
        if not owner or await _has_open_job(session, conn.workspace_id, ref):
            continue
        repo = await _only_repo(session, conn.workspace_id)
        job = await _open_job(session, AgentJob(
            workspace_id=conn.workspace_id, owner_user_id=owner.id,
            repo_id=repo.id if repo else None, source=AgentJobSource.LINEAR.value,
            ticket_ref=ref, ticket_url=issue.get("url"), external_id=issue.get("id"),
            connection_id=conn.id, acting_for=acting_for,
            title=(issue.get("title") or "").strip() or ref,
            issue_text=rules.issue_text(issue.get("title") or "", issue.get("description") or ""),
        ))
        created.append(job.id)
    return {"created": created}


async def _on_linear_comment(session: AsyncSession, comment_id: Optional[str]) -> dict:
    if not comment_id:
        return {"ignored": "no comment id"}
    decided = []
    for conn, creds in await _linear_connections(session):
        comment = linear_tickets.fetch_comment(creds, comment_id)
        if not comment or linear_tickets.is_photon_comment(comment.get("body") or ""):
            continue
        command = rules.parse_command(comment.get("body") or "")
        issue_uuid = (comment.get("issue") or {}).get("id")
        if not command or not issue_uuid:
            continue
        jobs = (await session.execute(select(AgentJob).where(
            AgentJob.workspace_id == conn.workspace_id, AgentJob.external_id == issue_uuid,
            AgentJob.status == AgentJobStatus.AWAITING_APPROVAL.value))).scalars().all()
        email = (comment.get("user") or {}).get("email")
        for job in jobs:
            actor, _ = await _owner_for(session, job.workspace_id, email, by="email") if email else (None, "")
            if not actor:
                continue
            if actor.id != job.owner_user_id and not await _is_workspace_owner(session, job.workspace_id, actor.id):
                continue
            try:
                await decide(session, job, command, actor.id)
                decided.append(job.id)
            except HTTPException:
                continue
    return {"decided": decided}


async def _on_comment(session: AsyncSession, event: dict) -> dict:
    if event.get("action") != "created":
        return {"ignored": event.get("action")}
    comment = event.get("comment") or {}
    if (comment.get("user") or {}).get("type") == "Bot":
        return {"ignored": "bot"}
    command = rules.parse_command(comment.get("body") or "")
    if not command:
        return {"ignored": "no command"}
    ref = f"{(event.get('repository') or {}).get('full_name')}#{(event.get('issue') or {}).get('number')}"
    login = (comment.get("user") or {}).get("login")
    result = await session.execute(select(AgentJob).where(
        AgentJob.ticket_ref == ref,
        AgentJob.status == AgentJobStatus.AWAITING_APPROVAL.value))
    decided = []
    for job in result.scalars().all():
        actor = await _member_by_login(session, job.workspace_id, login)
        if not actor:
            continue
        if actor.id != job.owner_user_id and not await _is_workspace_owner(session, job.workspace_id, actor.id):
            continue
        try:
            await decide(session, job, command, actor.id)
            decided.append(job.id)
        except HTTPException:
            continue
    return {"decided": decided}
