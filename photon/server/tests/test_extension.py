"""The Chrome extension's credential — what it can and cannot reach.

The token lives in a browser extension's storage, so the tests that matter
are denials: a route outside whisper, a different workspace, a revoked
browser. No database: the device lookup is a stub with the same interface.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from jose import jwt
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import auth  # noqa: E402
from app.models import ExtensionDevice  # noqa: E402
from app.routers.extension import code_hash, meet_code, new_code  # noqa: E402


def _request(path: str, workspace: str | None) -> Request:
    headers = [(b"x-workspace-id", workspace.encode())] if workspace else []
    return Request({"type": "http", "method": "GET", "path": path, "query_string": b"",
                    "headers": headers})


class _Session:
    def __init__(self, device):
        self.device = device

    async def get(self, model, key):
        return self.device if self.device and self.device.id == key else None

    def add(self, obj):
        pass

    async def commit(self):
        pass


def _payload(device_id="dev1", ws="ws1"):
    token, _ = auth.create_extension_token("u1", "a@b.c", ws, device_id)
    return jwt.decode(token, auth.settings.jwt_secret_key, algorithms=[auth.settings.jwt_algorithm])


def _check(path, workspace, device):
    return asyncio.run(auth._check_extension_token(_payload(), _request(path, workspace), _Session(device)))


def test_token_carries_scope_workspace_and_device():
    p = _payload()
    assert p["scope"] == auth.EXTENSION_SCOPE and p["ws"] == "ws1" and p["jti"] == "dev1"


def test_whisper_routes_in_its_workspace_are_allowed_and_touch_last_seen():
    device = ExtensionDevice(id="dev1", user_id="u1", workspace_id="ws1")
    _check("/api/whisper/sessions/s1/lines", "ws1", device)
    assert device.last_seen_at is not None


@pytest.mark.parametrize("path", [
    "/api/extension/meet-session",
    "/api/extension/me",
    "/api/whisper/sessions/s1/thread",
    "/api/whisper/sessions/s1/lines",
    "/api/whisper/threads/t1/messages",
    "/api/whisper/threads/t1/ask",
])
def test_exactly_the_routes_the_extension_calls_are_allowed(path):
    _check(path, "ws1", ExtensionDevice(id="dev1", user_id="u1", workspace_id="ws1"))


@pytest.mark.parametrize("path", [
    "/api/repos", "/api/agent-jobs", "/api/workspaces/settings", "/api/escalations/mine", "/api/meetings",
    # A path PREFIX used to be the rule, which let a leaked token mint new
    # pairing codes (devices that outlive revoking the leaked one) ...
    "/api/extension/pair-codes", "/api/extension/devices", "/api/extension/devices/dev2",
    # ... dispatch paid meeting bots, read another session's webhook secret,
    # or end someone else's session.
    "/api/whisper/join", "/api/whisper/sessions", "/api/whisper/sessions/s1/webhook",
    "/api/whisper/sessions/s1/bot", "/api/whisper/sessions/s1/end",
    "/api/whisper/sessions/s1/lines/extra",
])
def test_everything_else_is_refused(path):
    device = ExtensionDevice(id="dev1", user_id="u1", workspace_id="ws1")
    with pytest.raises(HTTPException) as err:
        _check(path, "ws1", device)
    assert err.value.status_code == 401


def test_another_workspace_or_none_is_refused():
    device = ExtensionDevice(id="dev1", user_id="u1", workspace_id="ws1")
    for ws in ("ws2", None):
        with pytest.raises(HTTPException):
            _check("/api/whisper/sessions/s1/lines", ws, device)


def test_a_revoked_or_unknown_browser_is_refused():
    revoked = ExtensionDevice(id="dev1", user_id="u1", workspace_id="ws1", revoked_at=datetime.utcnow())
    for device in (revoked, None):
        with pytest.raises(HTTPException) as err:
            _check("/api/whisper/sessions/s1/lines", "ws1", device)
        assert err.value.status_code == 401


def test_codes_are_typed_by_hand():
    code = new_code()
    assert len(code) == 9 and code[4] == "-"
    assert not set(code.replace("-", "")) & set("01OIL")
    assert code_hash("abcd efgh") == code_hash("ABCD-EFGH")


def test_meet_code():
    assert meet_code("https://meet.google.com/abc-defg-hij?authuser=0") == "abc-defg-hij"
    assert meet_code("https://meet.google.com/landing") is None


def test_a_scoped_token_never_skips_a_waiting_room():
    """meetings._optional_user decodes tokens itself; it must not treat the
    extension's token as a signed-in member."""
    from app.routers.meetings import _optional_user

    token, _ = auth.create_extension_token("u1", "a@b.c", "ws1", "dev1")
    assert asyncio.run(_optional_user(None, f"Bearer {token}")) is None
