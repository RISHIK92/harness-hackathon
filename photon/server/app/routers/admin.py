"""Workspace health for its owner — what is connected, what is missing, and
what to do about each.

    GET /api/admin/status

One read, owner-only, so the Admin page shows real state rather than a
checklist of guesses. Every item carries `ok` and, when not ok, the next
step in plain words. Secrets are never returned — only whether they are set.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.core.workspace import require_role
from app.database import get_session
from app.models import (
    AgentJob,
    Escalation,
    ExtensionDevice,
    ExternalConnection,
    GitHubInstallation,
    Repo,
    RepoStatus,
    SlackInstallation,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from app.services import harness_client
from app.services.whisper.providers import is_configured as recall_configured

router = APIRouter()


def _public(url: str) -> bool:
    return not any(h in (url or "") for h in ("localhost", "127.0.0.1", "0.0.0.0"))


async def _count(session: AsyncSession, stmt) -> int:
    return int((await session.execute(stmt)).scalar() or 0)


@router.get("/status")
async def status(workspace: Workspace = Depends(require_role(WorkspaceRole.OWNER)),
                 session: AsyncSession = Depends(get_session)):
    s = get_settings()
    ws = workspace.id
    week = datetime.utcnow() - timedelta(days=7)

    github = await _count(session, select(func.count()).select_from(GitHubInstallation)
                          .where(GitHubInstallation.workspace_id == ws))
    slack = await _count(session, select(func.count()).select_from(SlackInstallation)
                         .where(SlackInstallation.workspace_id == ws))
    linear = await _count(session, select(func.count()).select_from(ExternalConnection)
                          .where(ExternalConnection.workspace_id == ws,
                                 ExternalConnection.provider == "linear"))
    public = _public(s.public_base_url)
    harness_up = harness_client.health()

    integrations = [
        {"key": "harness", "label": "Fix engine (harness)", "ok": harness_up,
         "detail": "Plans and fixes tickets" if harness_up else
                   "Not reachable — start it with scripts/dev.sh, and set HARNESS_SERVICE_TOKEN on both sides"},
        {"key": "github_app", "label": "GitHub App", "ok": bool(s.github_app_id) and github > 0,
         "detail": (f"{github} installation(s)" if github else "Installed nowhere yet — connect GitHub in Knowledge")
                   if s.github_app_id else "Not configured on this deployment"},
        {"key": "github_webhook", "label": "GitHub tickets → agent", "ok": bool(s.github_app_webhook_secret) and public,
         "detail": f"Issues labelled “{s.agent_fix_label}” go to the assignee's agent"
                   if s.github_app_webhook_secret and public else
                   "Needs GITHUB_APP_WEBHOOK_SECRET and a public PUBLIC_BASE_URL"},
        {"key": "linear_webhook", "label": "Linear tickets → agent", "ok": bool(s.linear_webhook_secret) and linear > 0,
         "detail": "Receiving Issue and Comment events" if s.linear_webhook_secret and linear else
                   ("Connect Linear in Knowledge" if not linear else
                    "Add a Linear webhook and set LINEAR_WEBHOOK_SECRET")},
        {"key": "slack", "label": "Slack pings", "ok": slack > 0,
         "detail": "Escalations DM the member — needs the chat:write scope" if slack else
                   "Connect Slack so escalations can DM people"},
        {"key": "recall", "label": "Meeting notetaker (Zoom / Teams)", "ok": recall_configured() and public,
         "detail": "Ready" if recall_configured() and public else
                   ("Set RECALL_API_KEY" if not recall_configured() else "Needs a public PUBLIC_BASE_URL")},
    ]

    counts = {
        "members": await _count(session, select(func.count()).select_from(WorkspaceMember)
                                .where(WorkspaceMember.workspace_id == ws)),
        "repos_ready": await _count(session, select(func.count()).select_from(Repo)
                                    .where(Repo.workspace_id == ws, Repo.status == RepoStatus.READY)),
        "jobs_open": await _count(session, select(func.count()).select_from(AgentJob)
                                  .where(AgentJob.workspace_id == ws,
                                         AgentJob.status.in_(("draft", "planning", "awaiting_approval", "fixing")))),
        "prs_week": await _count(session, select(func.count()).select_from(AgentJob)
                                 .where(AgentJob.workspace_id == ws, AgentJob.status == "pr_open",
                                        AgentJob.updated_at >= week)),
        "escalations_week": await _count(session, select(func.count()).select_from(Escalation)
                                         .where(Escalation.workspace_id == ws, Escalation.created_at >= week)),
        "missed_week": await _count(session, select(func.count()).select_from(Escalation)
                                    .where(Escalation.workspace_id == ws, Escalation.created_at >= week,
                                           Escalation.status.in_(("expired", "answered_late")))),
        "extension_devices": await _count(session, select(func.count()).select_from(ExtensionDevice)
                                          .where(ExtensionDevice.workspace_id == ws,
                                                 ExtensionDevice.revoked_at.is_(None))),
    }
    return {"integrations": integrations, "counts": counts, "agent_fix_label": s.agent_fix_label}
