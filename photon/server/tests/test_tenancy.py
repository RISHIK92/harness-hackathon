"""The tenant boundary, exercised over real HTTP with two real users.

This is the test that matters most in the whole suite: a leak here means
one customer sees another customer's source code. It deliberately checks
DENIAL, not just that the happy path works — including that a non-member
gets 404 (never 403), so a workspace or repo id can't be probed for
existence.

Needs the API running (`uvicorn app.main:app --port 8000`); skips if not.
"""
import time

import httpx
import pytest

API = "http://localhost:8000"


def _api_up() -> bool:
    try:
        return httpx.get(f"{API}/health", timeout=3).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _api_up(), reason="brain-api not running on :8000")


@pytest.fixture(scope="module")
def users():
    """Two signed-up, logged-in users with fresh emails."""
    stamp = int(time.time() * 1000)
    creds = [
        {"email": f"alice{stamp}@example.test", "password": "hunter2hunter2"},
        {"email": f"bob{stamp}@example.test", "password": "hunter2hunter2"},
    ]
    out = []
    with httpx.Client(base_url=API, timeout=30) as c:
        for u in creds:
            assert c.post("/api/auth/signup", json=u).status_code == 201
            login = c.post("/api/auth/login", data={"username": u["email"], "password": u["password"]}).json()
            out.append({
                "headers": {"Authorization": f"Bearer {login['access_token']}"},
                "workspace": login["workspace"],
            })
    return out


@pytest.fixture(scope="module")
def alice_repo(users):
    with httpx.Client(base_url=API, timeout=30) as c:
        r = c.post("/api/repos", headers=users[0]["headers"],
                   json={"name": "tenancy-test-repo", "source_type": "github",
                         "source_url": "https://github.com/psf/requests"})
        assert r.status_code == 201, r.text
        return r.json()["id"]


def test_login_returns_a_workspace(users):
    assert users[0]["workspace"]["id"] and users[0]["workspace"]["name"]


def test_each_user_gets_their_own_personal_workspace(users):
    with httpx.Client(base_url=API, timeout=30) as c:
        a = c.get("/api/workspaces", headers=users[0]["headers"]).json()
        b = c.get("/api/workspaces", headers=users[1]["headers"]).json()
    assert len(a) == 1 and len(b) == 1
    assert a[0]["id"] != b[0]["id"]
    assert a[0]["role"] == "owner" and a[0]["is_personal"] is True


def test_owner_sees_their_repo(users, alice_repo):
    with httpx.Client(base_url=API, timeout=30) as c:
        repos = c.get("/api/repos", headers=users[0]["headers"]).json()
    assert any(r["id"] == alice_repo for r in repos)


def test_another_user_cannot_list_it(users, alice_repo):
    with httpx.Client(base_url=API, timeout=30) as c:
        repos = c.get("/api/repos", headers=users[1]["headers"]).json()
    assert not any(r["id"] == alice_repo for r in repos)


@pytest.mark.parametrize("method", ["get", "delete"])
def test_another_user_gets_404_not_403(users, alice_repo, method):
    # 404, not 403: a non-member must not be able to confirm the id exists.
    with httpx.Client(base_url=API, timeout=30) as c:
        r = getattr(c, method)(f"/api/repos/{alice_repo}", headers=users[1]["headers"])
    assert r.status_code == 404


def test_repo_is_invisible_from_the_owners_other_workspace(users, alice_repo):
    with httpx.Client(base_url=API, timeout=30) as c:
        team = c.post("/api/workspaces", headers=users[0]["headers"], json={"name": "Acme Support"})
        assert team.status_code == 201
        scoped = {**users[0]["headers"], "X-Workspace-Id": team.json()["id"]}
        assert c.get("/api/repos", headers=scoped).json() == []
        assert c.get(f"/api/repos/{alice_repo}", headers=scoped).status_code == 404


def test_cannot_borrow_someone_elses_workspace_id(users):
    with httpx.Client(base_url=API, timeout=30) as c:
        stolen = {**users[0]["headers"], "X-Workspace-Id": users[1]["workspace"]["id"]}
        assert c.get("/api/repos", headers=stolen).status_code == 404


def test_unauthenticated_is_rejected():
    with httpx.Client(base_url=API, timeout=30) as c:
        assert c.get("/api/repos").status_code == 401
