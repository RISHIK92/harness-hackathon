"""Does a call's configuration actually change what the agent can reach?

The assertions that matter are the negative ones: a source excluded from a
call must be unreachable even when the planner asks for it anyway — which
it does, which is why the loop refuses disabled tools rather than only
hiding them from the prompt.

Needs the API running; skips if not.
"""
import json

import httpx
import pytest

API = "http://localhost:8000"
WORKSPACE = "60c023a4-6642-4f9b-b6cb-616ad3115d87"
QUESTION = "what is our process when a customer reports webhook failures?"


def _api_up() -> bool:
    try:
        return httpx.get(f"{API}/health", timeout=3).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _api_up(), reason="brain-api not running on :8000")


@pytest.fixture(scope="module")
def client_and_headers():
    with httpx.Client(base_url=API, timeout=120) as c:
        login = c.post(
            "/api/auth/login",
            data={"username": "rishik@meridian.test", "password": "photon-demo-2026"},
        )
        if login.status_code != 200:
            pytest.skip("demo account not present on this deployment")
        headers = {
            "Authorization": f"Bearer {login.json()['access_token']}",
            "X-Workspace-Id": WORKSPACE,
        }
        yield c, headers


def _meeting(c, headers, **config) -> str:
    resp = c.post("/api/meetings", headers=headers, json=config)
    assert resp.status_code == 201, resp.text
    return resp.json()["slug"]


def _tools_used(c, slug: str) -> set[str]:
    """Tool names the turn actually started, plus any it was refused."""
    used: set[str] = set()
    with c.stream("POST", "/api/agent/ask/stream", json={"question": QUESTION, "meeting_slug": slug}) as r:
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event["type"] == "tool.start":
                used.add(event["tool"])
            if event["type"] == "tool.blocked":
                used.add(f"BLOCKED:{event['tool']}")
    return used


def test_enabled_source_is_reachable(client_and_headers):
    c, headers = client_and_headers
    slug = _meeting(c, headers, title="all", enabled_sources=["custom_docs", "slack"])
    assert "search_custom_docs" in _tools_used(c, slug)


def test_excluded_source_is_never_called(client_and_headers):
    c, headers = client_and_headers
    slug = _meeting(c, headers, title="slack only", enabled_sources=["slack"])
    used = _tools_used(c, slug)
    assert "search_custom_docs" not in used, "a disabled source was reached"


def test_no_sources_reaches_no_connector(client_and_headers):
    c, headers = client_and_headers
    slug = _meeting(c, headers, title="none", enabled_sources=[])
    used = _tools_used(c, slug)
    assert not {t for t in used if t in {"search_custom_docs", "search_slack", "search_jira"}}


def test_config_persists_and_can_change_mid_call(client_and_headers):
    c, headers = client_and_headers
    slug = _meeting(c, headers, title="KT", bot_types=["knowledge_transfer"],
                    enabled_sources=["custom_docs"])
    meeting = c.get(f"/api/meetings/{slug}", headers=headers).json()
    assert meeting["bot_types"] == ["knowledge_transfer"]
    assert meeting["language_mode"] == "english"

    patched = c.patch(
        f"/api/meetings/{slug}/config",
        headers=headers,
        json={"language_mode": "multilingual", "enabled_sources": ["custom_docs", "slack"]},
    ).json()
    assert patched["language_mode"] == "multilingual"
    assert patched["enabled_sources"] == ["custom_docs", "slack"]
