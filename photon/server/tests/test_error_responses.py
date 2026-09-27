"""A server crash must reach the browser as a readable error, not as
"Failed to fetch".

Starlette's last-resort 500 is produced outside the CORS middleware, so it
carries no Access-Control-Allow-Origin header and the browser discards it —
every crash looked exactly like the API being down. These drive a crashing
route through the real app and check what a cross-origin browser would see.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402


class _VoyageStyleError(Exception):
    pass


_VoyageStyleError.__module__ = "voyageai.error"


def _boom():
    raise RuntimeError("something broke")


def _no_key():
    raise _VoyageStyleError("No API key provided. You can set your API key …")


app.add_api_route("/__test/boom", _boom)
app.add_api_route("/__test/no-key", _no_key)
# Not used as a context manager, so the app's lifespan (database, Redis)
# never starts — only the middleware stack and these routes run.
client = TestClient(app, raise_server_exceptions=False)
ORIGIN = {"Origin": "http://localhost:3000"}


def test_a_crash_is_json_with_cors_headers():
    r = client.get("/__test/boom", headers=ORIGIN)
    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") in ("*", "http://localhost:3000")
    assert "RuntimeError" in r.json()["detail"]
    assert "something broke" not in r.json()["detail"]      # internals stay in the log


def test_a_missing_embeddings_key_says_what_to_set():
    r = client.get("/__test/no-key", headers=ORIGIN)
    assert r.status_code == 503
    assert "VOYAGE_API_KEY" in r.json()["detail"]
    assert r.headers.get("access-control-allow-origin")
