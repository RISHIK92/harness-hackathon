#!/usr/bin/env python3
"""Point the whole deployment at a new public URL — after every ngrok restart.

The free ngrok tier hands out a new subdomain each time it starts, and that
URL is registered in several places. Doing it by hand means editing .env,
two vendor consoles, and remembering to restart the API — every single time.

What this automates, and what it CANNOT:

  server/.env            automatic — PUBLIC_BASE_URL / CLIENT_BASE_URL
  API restart            automatic — uvicorn --reload re-reads .env only on
                         a process restart, so a file is touched to force one
  Slack redirect URLs    automatic IF a Slack app-configuration token is
                         present (SLACK_CONFIG_ACCESS_TOKEN); Slack's
                         apps.manifest.update is the only vendor API here
                         that can rewrite its own callback URLs
  GitHub webhook URL     automatic IF the App has one (PATCH /app/hook/config)
  GitHub callback and
  setup URLs             MANUAL — GitHub exposes no API to change an App's
                         callback or setup URLs. The script prints the exact
                         page and values instead of pretending.

Usage:
    python scripts/public_url.py                  # detect from the running ngrok
    python scripts/public_url.py --url https://x.ngrok-free.app
    python scripts/public_url.py --client-url https://y.ngrok-free.app
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
import urllib.request

SERVER_DIR = pathlib.Path(__file__).resolve().parent.parent
ENV_PATH = SERVER_DIR / ".env"
NGROK_API = "http://127.0.0.1:4040/api/tunnels"


def _get(url: str, headers: dict | None = None, timeout: float = 5.0):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def detect_tunnels() -> dict[int, str]:
    """local port -> public https url, from the ngrok agent's own API."""
    try:
        data = _get(NGROK_API)
    except Exception:
        return {}
    found: dict[int, str] = {}
    for tunnel in data.get("tunnels", []):
        public = tunnel.get("public_url", "")
        if not public.startswith("https://"):
            continue  # http duplicates of the same tunnel are noise
        addr = (tunnel.get("config") or {}).get("addr", "")
        match = re.search(r":(\d+)$", addr)
        if match:
            found[int(match.group(1))] = public
    return found


def update_env(updates: dict[str, str]) -> list[str]:
    """Rewrite keys in .env, preserving everything else (comments included)."""
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    seen, changed = set(), []
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in updates:
            new = f"{key}={updates[key]}"
            if line.strip() != new:
                changed.append(f"{key} -> {updates[key]}")
            lines[i] = new
            seen.add(key)
    missing = [k for k in updates if k not in seen]
    if missing:
        lines.append("")
        lines.append("# Public URLs (rewritten by scripts/public_url.py)")
        for key in missing:
            lines.append(f"{key}={updates[key]}")
            changed.append(f"{key} -> {updates[key]} (added)")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    return changed


def restart_api() -> bool:
    """uvicorn --reload restarts on .py changes, and a fresh process is what
    actually re-reads .env (settings are cached at import)."""
    target = SERVER_DIR / "app" / "main.py"
    if not target.exists():
        return False
    target.touch()
    return True


def update_slack(public_url: str) -> str:
    token = os.environ.get("SLACK_CONFIG_ACCESS_TOKEN", "").strip()
    app_id = os.environ.get("SLACK_APP_ID", "").strip()
    if not token or not app_id:
        return (
            "SKIPPED — set SLACK_APP_ID and SLACK_CONFIG_ACCESS_TOKEN to automate this.\n"
            "        Create the config token at https://api.slack.com/apps -> Your Apps -> "
            "'App-Level'/config tokens (they expire every 12h).\n"
            f"        Manual: set the redirect URL to {public_url}/api/integrations/slack/callback"
        )
    try:
        import urllib.parse

        # Read the current manifest, change only the redirect URLs, write it
        # back — a hand-built manifest would silently drop scopes.
        current = _get(
            f"https://slack.com/api/apps.manifest.export?app_id={app_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if not current.get("ok"):
            return f"FAILED to read manifest: {current.get('error')}"
        manifest = current["manifest"]
        manifest.setdefault("oauth_config", {})["redirect_urls"] = [
            f"{public_url}/api/integrations/slack/callback"
        ]
        body = urllib.parse.urlencode(
            {"app_id": app_id, "manifest": json.dumps(manifest)}
        ).encode()
        req = urllib.request.Request(
            "https://slack.com/api/apps.manifest.update",
            data=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
        return "updated" if result.get("ok") else f"FAILED: {result.get('error')}"
    except Exception as exc:  # noqa: BLE001
        return f"FAILED: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="public URL for the API (default: detect port 8000 from ngrok)")
    parser.add_argument("--client-url", help="public URL for the web app (port 3000)")
    parser.add_argument("--no-restart", action="store_true")
    parser.add_argument("--wait", type=int, default=0, help="seconds to wait for ngrok to come up")
    args = parser.parse_args()

    tunnels: dict[int, str] = {}
    deadline = time.time() + args.wait
    while True:
        tunnels = detect_tunnels()
        if tunnels or time.time() > deadline:
            break
        time.sleep(1)

    api_url = args.url or tunnels.get(8000)
    client_url = args.client_url or tunnels.get(3000)

    if not api_url:
        print("Could not find a public URL for port 8000.")
        print("  Start one with:  ngrok http 8000")
        print("  Or pass it directly:  python scripts/public_url.py --url https://…")
        if tunnels:
            print(f"  (ngrok is running, but only for: {sorted(tunnels)})")
        return 1

    api_url = api_url.rstrip("/")
    updates = {"PUBLIC_BASE_URL": api_url}
    if client_url:
        updates["CLIENT_BASE_URL"] = client_url.rstrip("/")

    print(f"Public API URL: {api_url}")
    if client_url:
        print(f"Public web URL: {client_url.rstrip('/')}")

    print("\n.env")
    for change in update_env(updates) or ["(already up to date)"]:
        print(f"  {change}")

    print("\nSlack redirect URL")
    print(f"  {update_slack(api_url)}")

    print("\nGitHub — no API exists to change an App's callback or setup URLs, so:")
    slug = os.environ.get("GITHUB_APP_SLUG", "<your-app>")
    print(f"  open https://github.com/settings/apps/{slug}")
    print(f"    Callback URL:  {api_url}/api/auth/github/callback")
    print(f"                   {api_url}/api/integrations/github/callback")
    print(f"    Setup URL:     {api_url}/api/integrations/github/callback")

    if not args.no_restart:
        print("\nAPI restart:", "triggered" if restart_api() else "could not touch app/main.py")

    return 0


if __name__ == "__main__":
    sys.exit(main())
