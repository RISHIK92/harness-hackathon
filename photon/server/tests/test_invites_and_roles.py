"""Invite codes, owner approval, and the three roles — over real HTTP.

Nearly every assertion here is about someone being STOPPED: a code alone
must not grant access, a pending user must stay out, a viewer must not
import, a member must not connect an integration, a revoked code must die,
and the last owner must not be removable — a workspace with no owner can
never be administered again.

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


def _signup(client: httpx.Client, tag: str, stamp: int) -> dict:
    creds = {"email": f"{tag}{stamp}@example.test", "password": "hunter2hunter2"}
    assert client.post("/api/auth/signup", json=creds).status_code == 201
    login = client.post(
        "/api/auth/login", data={"username": creds["email"], "password": creds["password"]}
    ).json()
    return {
        "headers": {"Authorization": f"Bearer {login['access_token']}"},
        "email": creds["email"],
    }


@pytest.fixture(scope="module")
def team():
    """An owner with a team workspace, plus a would-be member and an outsider."""
    stamp = int(time.time() * 1000)
    with httpx.Client(base_url=API, timeout=30) as c:
        owner = _signup(c, "owner", stamp)
        member = _signup(c, "member", stamp)
        outsider = _signup(c, "outsider", stamp)
        ws = c.post("/api/workspaces", headers=owner["headers"], json={"name": "Acme Support"}).json()
        yield {
            "client_kwargs": {"base_url": API, "timeout": 30},
            "workspace": ws,
            "owner": {**owner, "scoped": {**owner["headers"], "X-Workspace-Id": ws["id"]}},
            "member": {**member, "scoped": {**member["headers"], "X-Workspace-Id": ws["id"]}},
            "outsider": outsider,
        }


def test_invite_approval_and_roles(team):
    ws, owner, member, outsider = team["workspace"], team["owner"], team["member"], team["outsider"]
    with httpx.Client(**team["client_kwargs"]) as c:
        # ── an owner mints a code; a non-member cannot ──────────────────
        code = c.post("/api/workspaces/invite", headers=owner["scoped"]).json()["code"]
        assert code
        assert c.post("/api/workspaces/invite", headers=member["scoped"]).status_code == 404

        # ── the code buys a REQUEST, not access ─────────────────────────
        assert c.post("/api/workspaces/join", headers=member["headers"], json={"code": code}).json()["status"] == "pending"
        assert c.get("/api/repos", headers=member["scoped"]).status_code == 404

        requests = c.get("/api/workspaces/requests", headers=owner["scoped"]).json()
        assert [r["email"] for r in requests] == [member["email"]]

        # ── approval as VIEWER lets them in, but not to change anything ─
        c.post(f"/api/workspaces/requests/{requests[0]['id']}",
               headers=owner["scoped"], json={"approve": True, "role": "viewer"})
        assert c.get("/api/repos", headers=member["scoped"]).status_code == 200
        new_repo = {"name": "x", "source_type": "github", "source_url": "https://github.com/psf/requests"}
        assert c.post("/api/repos", headers=member["scoped"], json=new_repo).status_code == 403

        # ── promoted to MEMBER: may import, still may not connect sources ─
        members = c.get("/api/workspaces/members", headers=owner["scoped"]).json()
        member_id = next(m["user_id"] for m in members if m["email"] == member["email"])
        c.patch(f"/api/workspaces/members/{member_id}", headers=owner["scoped"], json={"role": "member"})
        assert c.post("/api/repos", headers=member["scoped"], json=new_repo).status_code == 201
        assert c.post("/api/integrations/github/connect", headers=member["scoped"]).status_code == 403

        # ── codes: invalid and revoked are indistinguishable, both dead ──
        assert c.post("/api/workspaces/join", headers=outsider["headers"], json={"code": "made-up"}).status_code == 404
        c.delete("/api/workspaces/invite", headers=owner["scoped"])
        assert c.post("/api/workspaces/join", headers=outsider["headers"], json={"code": code}).status_code == 404

        # ── the workspace can never be left ownerless ───────────────────
        owner_id = next(m["user_id"] for m in c.get("/api/workspaces/members", headers=owner["scoped"]).json()
                        if m["role"] == "owner")
        assert c.delete(f"/api/workspaces/members/{owner_id}", headers=owner["scoped"]).status_code == 409
        assert len(c.get("/api/workspaces/members", headers=member["scoped"]).json()) == 2
