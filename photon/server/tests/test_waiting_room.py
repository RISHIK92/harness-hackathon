"""Can a stranger with the code get into a call without being let in?

Every assertion here is about the door holding. The waiting room is only
real if a join TOKEN cannot be obtained without passing through it, so this
exercises the token route as well as the knock endpoints.

Needs the API on :8000 and the web app on :3000; skips otherwise.
"""
import httpx
import pytest

API = "http://localhost:8000"
WEB = "http://localhost:3000"


def _up(url: str) -> bool:
    try:
        return httpx.get(url, timeout=3).status_code < 500
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (_up(f"{API}/health") and _up(WEB)), reason="api or web app not running"
)


@pytest.fixture(scope="module")
def call():
    with httpx.Client(timeout=30) as c:
        login = c.post(
            f"{API}/api/auth/login",
            data={"username": "rishik@meridian.test", "password": "photon-demo-2026"},
        )
        if login.status_code != 200:
            pytest.skip("demo account not present")
        headers = {
            "Authorization": f"Bearer {login.json()['access_token']}",
            "X-Workspace-Id": "60c023a4-6642-4f9b-b6cb-616ad3115d87",
        }
        meeting = c.post(f"{API}/api/meetings", headers=headers, json={"title": "waiting room test"})
        if meeting.status_code != 201:
            pytest.skip("workspace has no sources, so a call cannot be started")
        yield c, headers, meeting.json()["slug"]


def test_member_is_admitted_without_queueing(call):
    c, headers, slug = call
    knock = c.post(
        f"{API}/api/meetings/{slug}/knock",
        headers={"Authorization": headers["Authorization"]},
        json={"display_name": "Rishik"},
    ).json()
    assert knock["status"] == "admitted"


def test_stranger_waits_and_cannot_mint_a_token(call):
    c, headers, slug = call
    knock = c.post(f"{API}/api/meetings/{slug}/knock", json={"display_name": "Client A"}).json()
    assert knock["status"] == "pending"

    pending = c.get(f"{WEB}/api/livekit-token", params={"room": slug, "name": "Client A", "knock": knock["id"]})
    assert pending.status_code == 403, "a pending guest was issued a join token"

    no_knock = c.get(f"{WEB}/api/livekit-token", params={"room": slug, "name": "Sneaky"})
    assert no_knock.status_code == 403, "a guest joined without asking at all"


def test_admitting_opens_the_door(call):
    c, headers, slug = call
    knock = c.post(f"{API}/api/meetings/{slug}/knock", json={"display_name": "Client B"}).json()
    waiting = c.get(f"{API}/api/meetings/{slug}/knocks", headers=headers).json()
    assert any(k["display_name"] == "Client B" for k in waiting)

    c.post(f"{API}/api/meetings/{slug}/knocks/{knock['id']}", headers=headers, json={"admit": True})
    assert c.get(f"{API}/api/meetings/{slug}/knock/{knock['id']}").json()["status"] == "admitted"

    token = c.get(f"{WEB}/api/livekit-token", params={"room": slug, "name": "Client B", "knock": knock["id"]})
    assert token.status_code == 200


def test_denied_guest_stays_out(call):
    c, headers, slug = call
    knock = c.post(f"{API}/api/meetings/{slug}/knock", json={"display_name": "Not welcome"}).json()
    c.post(f"{API}/api/meetings/{slug}/knocks/{knock['id']}", headers=headers, json={"admit": False})
    denied = c.get(f"{WEB}/api/livekit-token", params={"room": slug, "name": "Not welcome", "knock": knock["id"]})
    assert denied.status_code == 403


def test_waiting_list_is_not_public(call):
    c, _headers, slug = call
    # The list is people's names — it needs membership, not just the code.
    assert c.get(f"{API}/api/meetings/{slug}/knocks").status_code in (401, 403)
