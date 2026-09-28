"""Agent jobs on the worker: ask the harness for a plan, then for the fix.

Two tasks, with a human between them. `plan_agent_job` ends at
AWAITING_APPROVAL; only the owner's approval (console or a /approve on the
ticket) enqueues `fix_agent_job`. Neither task ever widens the scope the
owner approved, and neither merges anything.

Tokens are minted per call from the GitHub App installation and handed
straight to the harness service — they are never written to a row.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import structlog
from sqlmodel import Session

from app.config import get_settings
from app.database import get_sync_engine
from app.models import AgentJob, AgentJobSource, AgentJobStatus, Repo, User
from app.services import agent_jobs as rules
from app.services import harness_client as harness
from app.tasks.celery_app import celery_app

log = structlog.get_logger()


def _token_for(repo: Repo) -> Optional[str]:
    """The GitHub App installation that the repo was imported through — and
    nothing else. The deployment-wide GITHUB_TOKEN used to be the fallback,
    so a member who registered ANY repo URL could have Photon plan against,
    read, and open pull requests on whatever that token could reach,
    including other tenants' private repositories. A repo without an
    installation can still be planned against if it is public (the clone
    needs no token); it cannot be published to."""
    if repo.github_installation_id:
        from app.services.github_app_auth import get_installation_token
        return get_installation_token(repo.github_installation_id)
    return None


def _save(job_id: str, **fields) -> AgentJob:
    with Session(get_sync_engine()) as session:
        job = session.get(AgentJob, job_id)
        for key, value in fields.items():
            setattr(job, key, value)
        job.updated_at = datetime.utcnow()
        session.add(job)
        session.commit()
        session.refresh(job)
        return job


def _load(job_id: str) -> tuple[AgentJob, Repo, Optional[User]]:
    with Session(get_sync_engine()) as session:
        job = session.get(AgentJob, job_id)
        if not job:
            raise LookupError(f"agent job {job_id} not found")
        repo = session.get(Repo, job.repo_id) if job.repo_id else None
        owner = session.get(User, job.owner_user_id)
        session.expunge_all()
        return job, repo, owner


def _comment(job: AgentJob, repo: Optional[Repo], body: str) -> None:
    """Best effort: a comment that fails to post must not fail the job —
    the console shows the same thing."""
    if job.source == AgentJobSource.LINEAR.value:
        _linear_comment(job, body)
        return
    if job.source != AgentJobSource.GITHUB.value or not job.ticket_ref or not repo:
        return
    try:
        token = _token_for(repo)
        if token:
            rules.post_github_comment(token, job.ticket_ref, body)
    except Exception as exc:
        log.warning("agent_job.comment_failed", job_id=job.id, error=str(exc)[:200])


def _linear_comment(job: AgentJob, body: str) -> None:
    import json

    from app.core.crypto import decrypt
    from app.models import ExternalConnection
    from app.services import linear_tickets
    from app.services.escalation import first_name

    if not job.external_id or not job.connection_id:
        return
    try:
        with Session(get_sync_engine()) as session:
            conn = session.get(ExternalConnection, job.connection_id)
            owner = session.get(User, job.owner_user_id)
            raw = decrypt(conn.credentials_encrypted) if conn else ""
        if not raw:
            return
        name = first_name(None, owner.email if owner else None, owner.github_login if owner else None)
        linear_tickets.post_comment(json.loads(raw), job.external_id, linear_tickets.signed(name, body))
    except Exception as exc:
        log.warning("agent_job.linear_comment_failed", job_id=job.id, error=str(exc)[:200])


def _console_url(job_id: str) -> str:
    return f"{get_settings().client_base_url.rstrip('/')}/agent?job={job_id}"


@celery_app.task(bind=True, name="app.tasks.agent_jobs.plan_agent_job")
def plan_agent_job(self, job_id: str) -> dict:
    job, repo, owner = _load(job_id)
    if not repo or not repo.source_url:
        _save(job_id, status=AgentJobStatus.FAILED.value,
              escalation_reason="the job's repository has no clone URL")
        return {"status": "failed"}
    try:
        run_id = harness.start_plan(job.issue_text, repo.source_url, _token_for(repo))
        _save(job_id, plan_run_id=run_id)
        run = harness.wait(run_id, get_settings().harness_run_timeout_s)
    except harness.HarnessUnavailable as exc:
        _save(job_id, status=AgentJobStatus.FAILED.value, escalation_reason=str(exc))
        return {"status": "failed"}

    summary = rules.summarize_plan(run.get("artifacts") or {})
    blocked = (run.get("error") if run.get("status") != "done" else None) \
        or rules.plan_is_actionable(summary)
    if blocked:
        job = _save(job_id, plan=summary, status=AgentJobStatus.ESCALATED.value,
                    escalation_reason=f"no plan to approve: {blocked}")
        _comment(job, repo, f"**Photon** could not plan a fix: {blocked}. "
                            f"Handing this back to "
                            f"{'@' + owner.github_login if owner and owner.github_login else 'the assignee'}.")
        return {"status": job.status}

    job = _save(job_id, plan=summary, status=AgentJobStatus.AWAITING_APPROVAL.value)
    _comment(job, repo, rules.render_plan_comment(
        job.title, summary, _console_url(job_id), owner.github_login if owner else None))
    return {"status": job.status, "files": summary["files"]}


@celery_app.task(bind=True, name="app.tasks.agent_jobs.fix_agent_job")
def fix_agent_job(self, job_id: str) -> dict:
    job, repo, owner = _load(job_id)
    if job.status != AgentJobStatus.FIXING.value or not job.plan_run_id:
        return {"status": job.status, "skipped": True}
    scope = job.approved_scope or {}
    try:
        run_id = harness.start_fix(job.plan_run_id, scope)
        _save(job_id, fix_run_id=run_id)
        run = harness.wait(run_id, get_settings().harness_run_timeout_s)
    except harness.HarnessUnavailable as exc:
        _save(job_id, status=AgentJobStatus.FAILED.value, escalation_reason=str(exc))
        return {"status": "failed"}

    artifacts = run.get("artifacts") or {}
    result = {
        "outcome": run.get("outcome"),
        "exit_code": run.get("exit_code"),
        "scope_check": run.get("scope_check"),
        "confidence": artifacts.get("confidence"),
        "verification": artifacts.get("verification"),
        "diff": (artifacts.get("diff") or "")[:200_000],
    }
    action, why = rules.decide(run)
    login = owner.github_login if owner else None
    if action == "escalate":
        job = _save(job_id, result=result, status=AgentJobStatus.ESCALATED.value,
                    escalation_reason=why)
        _comment(job, repo, f"**Photon** stopped before opening a PR: {why}. "
                            f"{'@' + login if login else 'The assignee'} — "
                            f"the report is in the console: {_console_url(job_id)}")
        return {"status": job.status, "reason": why}

    token = _token_for(repo)
    if not token:
        reason = ("verified, but this repository was not imported through the GitHub App, "
                  "so Photon has no permission to push a branch or open a pull request")
        job = _save(job_id, result=result, status=AgentJobStatus.ESCALATED.value, escalation_reason=reason)
        return {"status": job.status, "reason": reason}
    try:
        pr = harness.publish(
            run_id,
            token=token,
            branch=rules.branch_name(job_id, job.title),
            title=job.title if not job.ticket_ref else f"{job.title} ({job.ticket_ref})",
            body=rules.pr_body(title=job.title, ticket_ref=job.ticket_ref,
                               ticket_url=job.ticket_url, owner_login=login,
                               summary=job.plan or {}, scope=scope, run=run, why=why),
            reviewers=[login] if login else [],
            trailers=[f"On-behalf-of: @{login}"] if login else [],
            draft=action == "draft_pr",
        )
    except harness.HarnessUnavailable as exc:
        job = _save(job_id, result=result, status=AgentJobStatus.ESCALATED.value,
                    escalation_reason=f"verified, but publishing failed: {exc}")
        return {"status": job.status}

    job = _save(job_id, result=result, status=AgentJobStatus.PR_OPEN.value,
                pr_url=pr.get("url"))
    _comment(job, repo, f"**Photon** opened {pr.get('url')} for "
                        f"{'@' + login if login else 'review'} ({why}).")
    return {"status": job.status, "pr": pr.get("url")}
