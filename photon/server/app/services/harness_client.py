"""Client for the harness service (`python -m harness.service`).

Synchronous on purpose: every caller is a Celery task, which already owns a
worker process and has nothing else to do while a run is in flight. The
router never talks to the harness directly — it enqueues, the task waits.

A token handed to `start_plan` / `publish` is a GitHub App installation
token minted for that one call. The service uses it and drops it; it is
never stored on either side.
"""
from __future__ import annotations

import time
from typing import Optional

import httpx
import structlog

from app.config import get_settings

log = structlog.get_logger()

FINISHED = ("done", "failed", "cancelled")


class HarnessUnavailable(RuntimeError):
    """The service could not be reached or refused the request."""


def _client() -> httpx.Client:
    settings = get_settings()
    headers = {}
    if settings.harness_service_token:
        headers["Authorization"] = f"Bearer {settings.harness_service_token}"
    return httpx.Client(base_url=settings.harness_service_url.rstrip("/"),
                        headers=headers, timeout=30.0)


def _call(method: str, path: str, json: Optional[dict] = None) -> dict:
    try:
        with _client() as client:
            resp = client.request(method, path, json=json)
    except httpx.HTTPError as exc:
        raise HarnessUnavailable(f"harness service unreachable: {exc}") from None
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error", resp.text)
        except ValueError:
            detail = resp.text
        raise HarnessUnavailable(f"harness {resp.status_code}: {detail}")
    return resp.json()


def health() -> bool:
    try:
        return bool(_call("GET", "/v1/health").get("ok"))
    except HarnessUnavailable:
        return False


def start_plan(issue: str, repo_url: str, token: Optional[str] = None,
               ref: Optional[str] = None) -> str:
    repo = {"url": repo_url}
    if ref:
        repo["ref"] = ref
    body = {"mode": "plan", "issue": issue, "repo": repo}
    if token:
        body["token"] = token
    return _call("POST", "/v1/runs", body)["id"]


def start_fix(plan_run_id: str, scope: dict) -> str:
    return _call("POST", "/v1/runs", {"mode": "fix", "from_run": plan_run_id,
                                      "scope": scope or {}})["id"]


def get_run(run_id: str) -> dict:
    return _call("GET", f"/v1/runs/{run_id}")


def wait(run_id: str, timeout_s: int, poll_s: float = 5.0) -> dict:
    """Block until the run finishes. A timeout cancels it: a run nobody is
    waiting for anymore should not keep a checkout and a model busy."""
    end = time.monotonic() + timeout_s
    while True:
        run = get_run(run_id)
        if run["status"] in FINISHED:
            return run
        if time.monotonic() > end:
            try:
                _call("DELETE", f"/v1/runs/{run_id}")
            except HarnessUnavailable:
                pass
            run["status"] = "failed"
            run["error"] = f"no result after {timeout_s}s"
            return run
        time.sleep(poll_s)


def publish(run_id: str, *, token: str, branch: str, title: str, body: str,
            reviewers: list[str], trailers: list[str], draft: bool) -> dict:
    return _call("POST", f"/v1/runs/{run_id}/publish", {
        "token": token, "branch": branch, "title": title, "body": body,
        "reviewers": reviewers, "trailers": trailers, "draft": draft,
        "author": {"name": "Photon", "email": "photon@users.noreply.github.com"},
    })
