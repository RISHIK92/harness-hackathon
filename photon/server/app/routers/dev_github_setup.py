"""One-time GitHub App creation via GitHub's "manifest" flow. Not part of
the product — an operator visits /dev/github-app/new once, clicks
"Create GitHub App" on GitHub's own site, and gets redirected back here
with credentials to paste into .env. Never linked from the product UI;
mounted only outside production (see main.py).

Why this exists at all: registering a GitHub App normally means filling
out a multi-field form by hand on GitHub's site. The manifest flow lets
us pre-fill everything (name, permissions, redirect URLs) and reduces the
operator's part to one click.
"""
from __future__ import annotations

import html
import json

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.config import get_settings

log = structlog.get_logger()
router = APIRouter()
settings = get_settings()


@router.get("/github-app/new", response_class=HTMLResponse)
async def new_github_app_form():
    manifest = {
        "name": "Photon (dev)",
        "url": settings.client_base_url,
        # Where GitHub returns the one-time manifest-conversion code. This
        # is NOT an OAuth callback — the two are different fields and
        # GitHub rejects the manifest if you supply only this one while
        # also asking for user authorization.
        "redirect_url": f"{settings.public_base_url}/dev/github-app/callback",
        # Required because request_oauth_on_install is true. Both real
        # OAuth landing points must be listed, or GitHub refuses the
        # redirect at runtime with redirect_uri_mismatch:
        #   1. "Sign in with GitHub"  -> routers/auth.py     /api/auth/github/callback
        #   2. "Connect GitHub" install -> routers/github_app.py
        #      /api/integrations/github/callback (receives installation_id
        #      + our state nonce; the OAuth code it also gets is ignored)
        "callback_urls": [
            f"{settings.public_base_url}/api/auth/github/callback",
            f"{settings.public_base_url}/api/integrations/github/callback",
        ],
        # Where GitHub sends the user after INSTALLING the app, carrying
        # installation_id + our state nonce. This — not OAuth — is what the
        # install callback actually needs: it never reads an OAuth `code`,
        # it authenticates to GitHub as the App (JWT) to look the
        # installation up. Setting it explicitly also removes an ambiguity:
        # with request_oauth_on_install the post-install redirect goes to a
        # CALLBACK url, and with two registered there is no guarantee it
        # picks the install one rather than the sign-in one.
        "setup_url": f"{settings.public_base_url}/api/integrations/github/callback",
        "setup_on_update": True,
        "public": False,
        # Write on contents/pull_requests/issues is for agent jobs
        # (routers/agent_jobs.py): pushing an approved fix to a branch,
        # opening its PR, and posting the plan on the ticket. Never merge.
        "default_permissions": {
            "contents": "write",
            "metadata": "read",
            "pull_requests": "write",
            "issues": "write",
        },
        # A ticket labelled for the agent, and the owner's /approve on it.
        "default_events": ["issues", "issue_comment"],
        "hook_attributes": {
            "url": f"{settings.public_base_url}/api/integrations/github/webhook",
            "active": True,
        },
        # Deliberately false: "Sign in with GitHub" is its own flow through
        # the callback URLs above and works regardless of this setting.
        # Asking for identity during install just adds a prompt for data
        # the install path does not use.
        "request_oauth_on_install": False,
    }
    manifest_json = html.escape(json.dumps(manifest))
    # Auto-submitting form: GitHub's manifest flow is a POST with a
    # "manifest" field, there's no GET-with-querystring equivalent.
    return f"""
    <html><body>
      <p>Redirecting to GitHub to create the app…</p>
      <form id="f" action="https://github.com/settings/apps/new" method="post">
        <input type="hidden" name="manifest" value="{manifest_json}">
      </form>
      <script>document.getElementById('f').submit();</script>
    </body></html>
    """


@router.get("/github-app/callback", response_class=HTMLResponse)
async def github_app_manifest_callback(code: str = Query(...)):
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"https://api.github.com/app-manifests/{code}/conversions")
    if resp.status_code != 201:
        # Never log resp.text here — a manifest-conversion error response
        # does not contain the credentials, but there is no reason to risk it.
        raise HTTPException(status_code=502, detail="GitHub App conversion failed — the code may already be used or expired")

    data = resp.json()
    log.info("dev_github_setup.app_created", app_id=data.get("id"), slug=data.get("slug"))

    fields = [
        ("GITHUB_APP_ID", str(data.get("id", ""))),
        ("GITHUB_APP_SLUG", data.get("slug", "")),
        ("GITHUB_APP_CLIENT_ID", data.get("client_id", "")),
        ("GITHUB_APP_CLIENT_SECRET", data.get("client_secret", "")),
        ("GITHUB_APP_WEBHOOK_SECRET", data.get("webhook_secret", "")),
    ]
    pem = data.get("pem", "")
    pem_env_value = pem.replace("\n", "\\n")

    rows = "\n".join(f"<tr><td><code>{k}</code></td><td><code>{html.escape(v)}</code></td></tr>" for k, v in fields)
    return f"""
    <html><body style="font-family: monospace">
      <h2>GitHub App created: {html.escape(data.get('name', ''))}</h2>
      <p><strong>Copy these into server/.env now — GitHub will not show them again.</strong></p>
      <table border="1" cellpadding="6">{rows}
        <tr><td>GITHUB_APP_PRIVATE_KEY</td><td style="max-width:600px; word-break:break-all">{html.escape(pem_env_value)}</td></tr>
      </table>
      <p>Then restart the server.</p>
    </body></html>
    """
