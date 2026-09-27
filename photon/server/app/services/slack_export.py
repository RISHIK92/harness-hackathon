"""Import a Slack workspace export (.zip).

Exists because OAuth is not always available: a Slack app has to be
registered in some workspace, and letting *customers* install it requires
Slack's public distribution, which in turn requires HTTPS redirect URLs —
so a localhost or pre-deployment build cannot demo the OAuth path at all.

Any workspace OWNER can produce an export from Slack's admin UI without an
app, an admin approval, or a developer. Same destination as the live sync
(`index_messages`), so imported history is searched by exactly the same
code as connected history.

Export layout (Slack's standard format):
    users.json                 -> id, profile.display_name / real_name
    channels.json              -> id, name, is_private (sometimes absent)
    <channel-name>/YYYY-MM-DD.json -> a JSON ARRAY of messages for that day
"""
from __future__ import annotations

import json
import zipfile
from typing import Optional

import structlog

from app.services.slack_sync import index_messages

log = structlog.get_logger()

# Slack writes one file per channel per DAY, so a year of a busy channel is
# ~365 files. Read lazily and batch by channel rather than loading the whole
# export into memory.
_SKIP_SUBTYPES = {"channel_join", "channel_leave", "channel_topic", "channel_purpose"}


def _load_json(zf: zipfile.ZipFile, name: str):
    try:
        with zf.open(name) as fh:
            return json.load(fh)
    except (KeyError, json.JSONDecodeError):
        return None


def import_export(
    workspace_id: str, zip_path: str, only_channels: Optional[set[str]] = None
) -> dict:
    """Index an export. Returns per-channel counts."""
    results: dict[str, int] = {}
    with zipfile.ZipFile(zip_path) as zf:
        names_json = _load_json(zf, "users.json") or []
        directory = {}
        for member in names_json:
            profile = member.get("profile") or {}
            directory[member.get("id", "")] = (
                profile.get("display_name") or profile.get("real_name") or member.get("name") or member.get("id", "")
            )

        channels_json = _load_json(zf, "channels.json") or []
        channel_meta = {c.get("name", ""): c for c in channels_json}

        # Group the day-files by their channel folder.
        by_channel: dict[str, list[str]] = {}
        for entry in zf.namelist():
            if not entry.endswith(".json") or "/" not in entry:
                continue
            channel_name = entry.split("/", 1)[0]
            if only_channels and channel_name not in only_channels:
                continue
            by_channel.setdefault(channel_name, []).append(entry)

        for channel_name, files in by_channel.items():
            messages: list[dict] = []
            for entry in sorted(files):
                day = _load_json(zf, entry)
                if not isinstance(day, list):
                    continue
                for m in day:
                    if m.get("subtype") in _SKIP_SUBTYPES:
                        continue
                    if not (m.get("text") or "").strip():
                        continue
                    messages.append(m)
            if not messages:
                continue
            channel_id = (channel_meta.get(channel_name) or {}).get("id", f"export-{channel_name}")
            count = index_messages(workspace_id, channel_id, channel_name, messages, directory)
            results[channel_name] = count
            log.info("slack_export.channel_indexed", channel=channel_name, messages=count)

    return results


def inspect_export(zip_path: str) -> dict:
    """What's in this file, without indexing anything.

    Shown before importing so nobody discovers after the fact that they
    just embedded #random and a year of standups.
    """
    with zipfile.ZipFile(zip_path) as zf:
        entries = zf.namelist()
        channels: dict[str, int] = {}
        for entry in entries:
            if entry.endswith(".json") and "/" in entry:
                channels[entry.split("/", 1)[0]] = channels.get(entry.split("/", 1)[0], 0) + 1
        users = _load_json(zf, "users.json") or []
    return {
        "channels": sorted(
            ({"name": name, "day_files": days} for name, days in channels.items()),
            key=lambda c: -c["day_files"],
        ),
        "user_count": len(users),
        "looks_like_slack_export": "users.json" in entries or bool(channels),
    }
