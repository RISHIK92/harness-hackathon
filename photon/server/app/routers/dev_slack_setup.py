"""One-time Slack app creation via Slack's app-manifest flow. Not part of
the product — an operator opens /dev/slack-app/new once, is handed a
pre-filled manifest, and pastes the resulting credentials into .env.

Slack has no server-to-server "create app from manifest" exchange like
GitHub's (their manifest API needs an app-configuration token that itself
has to be created by hand), so this cannot be fully automatic. What it can
do is remove every chance to mistype a scope or a redirect URL: the
manifest below is exactly what this deployment expects, and Slack's
"from a manifest" flow accepts it verbatim.
"""
from __future__ import annotations

import html
import json

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.routers.slack import SLACK_SCOPES

router = APIRouter()
settings = get_settings()


@router.get("/slack-app/new", response_class=HTMLResponse)
async def new_slack_app_form():
    redirect_url = f"{settings.public_base_url}/api/integrations/slack/callback"
    manifest = {
        "display_information": {
            "name": "Photon",
            "description": "Answers support questions from your code, docs and Slack history.",
        },
        "features": {
            "bot_user": {"display_name": "Photon", "always_online": False},
        },
        "oauth_config": {
            "redirect_urls": [redirect_url],
            # Read-only. The agent never posts to Slack, so no write scopes
            # are requested — a reviewer approving this app can see that.
            "scopes": {"bot": SLACK_SCOPES.split(",")},
        },
        "settings": {
            "org_deploy_enabled": False,
            "socket_mode_enabled": False,
            # No event subscriptions: history is pulled on demand, so there
            # is no public URL for Slack to call. That also means this works
            # on localhost, which an events-based design would not.
            "token_rotation_enabled": False,
        },
    }
    manifest_json = json.dumps(manifest, indent=2)

    return f"""
    <html><body style="font-family: ui-monospace, monospace; max-width: 820px; margin: 40px auto; line-height: 1.5">
      <h2>Create the Slack app</h2>
      <ol>
        <li>Open <a href="https://api.slack.com/apps?new_app=1" target="_blank">api.slack.com/apps &rarr; Create New App</a>
            and choose <b>From a manifest</b>.</li>
        <li>Pick your Slack workspace, paste the JSON below, and create the app.</li>
        <li>On <b>Basic Information</b>, copy the Client ID, Client Secret and Signing Secret into
            <code>server/.env</code> as
            <code>SLACK_CLIENT_ID</code>, <code>SLACK_CLIENT_SECRET</code>,
            <code>SLACK_SIGNING_SECRET</code>, then restart the server.</li>
        <li>Back in Photon, press <b>Connect Slack</b>. Slack will ask which channels to add the bot to —
            it can only read channels it has been added to.</li>
      </ol>
      <p><b>Redirect URL this deployment expects:</b><br><code>{html.escape(redirect_url)}</code></p>
      <textarea readonly rows="26" style="width:100%; font-family: inherit; font-size: 12px"
        onclick="this.select()">{html.escape(manifest_json)}</textarea>
      <p style="color:#666">Scopes requested are read-only: {html.escape(SLACK_SCOPES)}</p>
    </body></html>
    """
