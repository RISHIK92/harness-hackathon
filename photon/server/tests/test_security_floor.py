"""Phase 0 — the security floor (ENTERPRISE_ARCHITECTURE.md §24, Appendix A).

Each test names the defect it holds shut. No database, no network, no
running server: these fail on the edit that would reopen the hole, which is
the only moment a test can stop it.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings, get_settings, validate_security  # noqa: E402
from app.core import service_auth  # noqa: E402


# ── D-01, D-02, D-03, D-08, D-09: every route is authenticated ───────────

# Routes that are public ON PURPOSE, each with the reason. Anything not
# here must depend on a user session, the repo-access check or the worker
# token. A new route that is none of those fails this test until someone
# decides, in review, that it belongs on this list.
PUBLIC = {
    ("POST", "/api/auth/signup"): "creating an account",
    ("POST", "/api/auth/login"): "signing in",
    ("GET", "/api/auth/github/login"): "OAuth start (sets a state cookie)",
    ("GET", "/api/auth/github/callback"): "OAuth return (checks the state cookie)",
    ("POST", "/api/meetings/{slug}/knock"): "a guest asking to be let in",
    ("GET", "/api/meetings/{slug}/knock/{knock_id}"): "the guest polling their own knock",
    ("GET", "/api/meetings/{slug}/admission/{knock_id}"): "the token route verifying an admission",
    ("GET", "/api/integrations/github/callback"): "GitHub App install return (single-use nonce)",
    ("GET", "/api/integrations/slack/callback"): "Slack OAuth return (single-use nonce)",
    ("POST", "/api/whisper/webhooks/{session_id}/{secret}"): "meeting-bot vendor webhook (per-session secret)",
    ("POST", "/api/integrations/github/webhook"): "GitHub webhook (HMAC)",
    ("POST", "/api/integrations/linear/webhook"): "Linear webhook (HMAC)",
    ("POST", "/api/extension/pair"): "trading a one-time pairing code for a token",
    ("GET", "/api/agent-worker/status"): "is a voice worker running (no host, no content)",
    ("GET", "/dev/github-app/new"): "dev-only manifest bootstrap (development only)",
    ("GET", "/dev/github-app/callback"): "dev-only manifest bootstrap (development only)",
    ("GET", "/dev/slack-app/new"): "dev-only manifest bootstrap (development only)",
    ("GET", "/health"): "liveness",
}


def _dependencies(dependant) -> set:
    found = set()
    for sub in dependant.dependencies:
        found.add(sub.call)
        found |= _dependencies(sub)
    return found


def test_every_route_is_authenticated_or_deliberately_public():
    from fastapi.routing import APIRoute

    import app.main as main
    from app.core import auth, repo_access

    guards = {auth.get_current_user, repo_access.require_repo_access, service_auth.worker_token_header}
    unguarded = set()
    for route in main.app.routes:
        if not isinstance(route, APIRoute) or route.path.startswith("/__test/"):
            continue                    # test_error_responses.py mounts probes on the shared app
        if _dependencies(route.dependant) & guards:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            unguarded.add((method, route.path))
    assert unguarded - set(PUBLIC) == set(), "routes with no authentication at all"


def test_the_legacy_query_route_and_raw_tool_dispatch_are_gone():
    """D-02/D-03: /api/query searched every tenant's code with a blank
    repo_id; POST /api/tools/{name} ran any tool with caller-chosen tenant ids."""
    import app.main as main

    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert not any(p.startswith("/api/query") for p in paths)
    assert "/api/tools/{tool_name}" not in paths


def test_an_unscoped_vector_search_is_refused_before_any_io():
    """D-03, in the core rather than in each caller."""
    from app.core.embedding.embedder import _sync_vector_search

    with pytest.raises(ValueError):
        _sync_vector_search(None, "anything", 5, workspace_id=None)


# ── D-08, D-09: the worker's meeting-scoped credential ───────────────────

@pytest.fixture
def worker_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "worker_service_token", "")
    monkeypatch.setattr(settings, "livekit_api_secret", "lk-secret-for-tests")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk-secret-for-tests")
    monkeypatch.delenv("WORKER_SERVICE_TOKEN", raising=False)


def _call_agent_auth():
    spec = importlib.util.spec_from_file_location(
        "call_agent_service_auth", ROOT.parent / "call-agent" / "service_auth.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("room", ["abcd-efgh", "ABCD EFGH", "abcdefgh", "ab2d-e9gh", "not-a-slug-at-all"])
def test_the_worker_and_the_api_derive_the_same_token(worker_key, room):
    worker = _call_agent_auth()
    assert worker.meeting_scope(room) == service_auth.meeting_scope(room)
    assert worker.token_for(worker.meeting_scope(room)) == service_auth.token_for(service_auth.meeting_scope(room))


def test_a_token_opens_only_its_own_meeting(worker_key):
    token = service_auth.token_for(service_auth.meeting_scope("abcd-efgh"))
    service_auth.require_worker(token, service_auth.meeting_scope("abcd-efgh"))
    with pytest.raises(HTTPException) as err:
        service_auth.require_worker(token, service_auth.meeting_scope("wxyz-2345"))
    assert err.value.status_code == 401
    with pytest.raises(HTTPException):
        service_auth.require_worker(None, service_auth.meeting_scope("abcd-efgh"))


def test_unconfigured_worker_auth_refuses_rather_than_falls_open(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "worker_service_token", "")
    monkeypatch.setattr(settings, "livekit_api_secret", "")
    with pytest.raises(HTTPException) as err:
        service_auth.require_worker("anything", service_auth.meeting_scope("abcd-efgh"))
    assert err.value.status_code == 503


# ── D-04, D-06: secrets and environment ──────────────────────────────────

def test_public_default_secrets_refuse_to_boot_outside_development():
    with pytest.raises(RuntimeError):
        validate_security(Settings(app_env="production", secret_key="changeme"))
    with pytest.raises(RuntimeError):
        validate_security(Settings(app_env="staging", secret_key="x" * 40,
                                   JWT_SECRET_KEY="change-me-in-production-jwt-secret"))
    validate_security(Settings(app_env="development", secret_key="changeme"))      # warns, boots
    validate_security(Settings(app_env="production", secret_key="a-real-secret-" * 3))


def test_the_jwt_key_is_derived_and_never_the_fernet_key():
    s = Settings(app_env="development", secret_key="a-real-secret-" * 3)
    assert s.jwt_secret_key and s.jwt_secret_key != s.secret_key
    assert Settings(app_env="development", secret_key="another-secret-" * 3).jwt_secret_key != s.jwt_secret_key


def test_cors_is_an_explicit_origin_list():
    """D-37: "*" with credentials is gone."""
    s = Settings(app_env="development", client_base_url="https://app.example.com/")
    assert s.allowed_origins == ["https://app.example.com"]
    s = Settings(app_env="development", cors_origins="https://a.example, https://b.example")
    assert s.allowed_origins == ["https://a.example", "https://b.example"]


# ── D-18: repository sources ─────────────────────────────────────────────

@pytest.mark.parametrize("url", ["https://github.com/acme/api", "https://github.com/acme/api.git"])
def test_github_urls_are_accepted(url):
    from app.models import RepoCreate, RepoSourceType
    from app.routers.repos import _validate_source

    _validate_source(RepoCreate(name="x", source_type=RepoSourceType.GITHUB, source_url=url))


@pytest.mark.parametrize("url", [
    "https://evil.example/acme/api",          # would have received the deployment's token
    "https://github.com.evil.example/a/b",
    "file:///etc",
    "https://github.com/acme",
    "",
])
def test_other_sources_are_refused(url):
    from app.models import RepoCreate, RepoSourceType
    from app.routers.repos import _validate_source

    with pytest.raises(HTTPException):
        _validate_source(RepoCreate(name="x", source_type=RepoSourceType.GITHUB, source_url=url))


def test_local_paths_only_in_development(monkeypatch):
    from app.models import RepoCreate, RepoSourceType
    from app.routers import repos

    payload = RepoCreate(name="x", source_type=RepoSourceType.LOCAL, source_url="/etc")
    monkeypatch.setattr(repos.settings, "app_env", "production")
    with pytest.raises(HTTPException):
        repos._validate_source(payload)
    monkeypatch.setattr(repos.settings, "app_env", "development")
    repos._validate_source(payload)


# ── D-26: Linear self-approval ───────────────────────────────────────────

def test_photon_authored_linear_comments_are_never_decisions():
    from app.services import agent_jobs as rules
    from app.services import linear_tickets

    body = linear_tickets.signed("Priya", "Plan:\n- change x\n/approve")
    assert rules.parse_command(body) is not None          # the text alone would approve...
    assert linear_tickets.is_photon_comment(body)          # ...so the webhook skips it first
    assert not linear_tickets.is_photon_comment("/approve looks good")


# ── D-15: fictional demo data stays out of real workspaces ──────────────

def test_demo_accounts_are_not_in_a_real_workspaces_plan_prompt():
    from app.agent.prompts import _format_accounts

    assert _format_accounts({"search_code", "search_slack"}) == "(none for this workspace)"
    assert "acct" in _format_accounts({"get_account"}).lower() or _format_accounts({"get_account"})


def test_search_slack_never_falls_back_to_the_seed_corpus(monkeypatch):
    from app.services import slack_sync
    from app.tools import knowledge

    monkeypatch.setattr(slack_sync, "has_data", lambda ws: False)

    async def seed_must_not_run(*a, **k):
        raise AssertionError("seed Slack searched for a real workspace")

    monkeypatch.setattr(knowledge, "kb_search", seed_must_not_run)
    result = asyncio.run(knowledge.search_slack("webhooks", workspace_id="ws-real"))
    assert result["status"] == "empty" and result["evidence"] == []


def test_explain_why_does_not_join_fixture_history_onto_a_real_repo(monkeypatch):
    from app.tools import provenance

    async def located(symbol, repo_id):
        return "app/main.py", []

    monkeypatch.setattr(provenance, "_locate_file", located)
    monkeypatch.setattr(provenance, "_seed_repo_id", lambda: "seed-repo")
    monkeypatch.setattr(provenance, "_commits_touching",
                        lambda path: (_ for _ in ()).throw(AssertionError("fixture commits read")))
    result = asyncio.run(provenance.explain_why("app/main.py", repo_id="real-repo"))
    assert [e["source_type"] for e in result["evidence"]] == ["code"]
    assert "not indexed" in (result["note"] or "")


# ── D-01: who may ask, and which tenant they get ─────────────────────────

class _Result:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def first(self):
        return self.value


class _AskSession:
    """Just enough session for _resolve_turn: one meeting, some repos."""

    def __init__(self, meeting=None, repos=None, workspaces=None):
        self.meeting, self.repos, self.workspaces = meeting, repos or {}, workspaces or {}

    async def execute(self, _stmt):
        return _Result(self.meeting)

    async def get(self, model, key):
        return {"Repo": self.repos, "Workspace": self.workspaces}.get(model.__name__, {}).get(key)


def _ask_env(monkeypatch, *, member_of=(), user=None):
    from app.core import workspace as ws_module
    from app.models import User
    from app.routers import agent

    async def membership(session, workspace_id, user_id):
        return object() if workspace_id in member_of else None

    async def bearer(session, authorization):
        return user if authorization == "Bearer good" else None

    async def meeting_config(session, meeting):
        return {"workspace_id": meeting.workspace_id, "allowed_tools": {"search_code"}}

    async def workspace_config(session, workspace):
        return {"workspace_id": workspace.id, "allowed_tools": {"search_code"}}

    monkeypatch.setattr(ws_module, "membership_for", membership)
    monkeypatch.setattr(agent, "_user_from_bearer", bearer)
    monkeypatch.setattr(agent, "_meeting_config", meeting_config)
    monkeypatch.setattr(agent, "_workspace_config", workspace_config)
    return agent, User


def _request(headers=None):
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "POST", "path": "/api/agent/ask", "query_string": b"",
                    "headers": raw})


def _resolve(agent, payload, session, authorization=None, worker=None, headers=None):
    return asyncio.run(agent._resolve_turn(_request(headers), payload, session, authorization, worker))


def test_no_principal_no_answer(monkeypatch):
    agent, _ = _ask_env(monkeypatch)
    with pytest.raises(HTTPException) as err:
        _resolve(agent, agent.AgentAskRequest(question="q", workspace_id="victim"), _AskSession())
    assert err.value.status_code == 401


def test_a_body_workspace_id_is_a_selection_not_a_grant(monkeypatch):
    from app.models import User

    user = User(id="u1", email="a@b.c")
    agent, _ = _ask_env(monkeypatch, member_of=("mine",), user=user)
    with pytest.raises(HTTPException) as err:
        _resolve(agent, agent.AgentAskRequest(question="q", workspace_id="victim"), _AskSession(),
                 authorization="Bearer good")
    assert err.value.status_code == 404
    from app.models import Workspace

    session = _AskSession(workspaces={"mine": Workspace(id="mine", name="Mine")})
    config = _resolve(agent, agent.AgentAskRequest(question="q", workspace_id="mine"), session,
                      authorization="Bearer good")
    assert config["workspace_id"] == "mine"


def test_a_member_of_another_workspace_cannot_ask_inside_this_meeting(monkeypatch):
    from app.models import Meeting, User

    agent, _ = _ask_env(monkeypatch, member_of=("mine",), user=User(id="u1", email="a@b.c"))
    session = _AskSession(meeting=Meeting(slug="abcd-efgh", workspace_id="theirs"))
    with pytest.raises(HTTPException) as err:
        _resolve(agent, agent.AgentAskRequest(question="q", meeting_slug="abcd-efgh"), session,
                 authorization="Bearer good")
    assert err.value.status_code == 404


def test_the_worker_needs_the_token_for_this_meeting(monkeypatch, worker_key):
    from app.models import Meeting

    agent, _ = _ask_env(monkeypatch)
    session = _AskSession(meeting=Meeting(slug="abcd-efgh", workspace_id="theirs"))
    payload = agent.AgentAskRequest(question="q", meeting_slug="abcd-efgh", workspace_id="victim")
    wrong = service_auth.token_for(service_auth.meeting_scope("wxyz-2345"))
    with pytest.raises(HTTPException) as err:
        _resolve(agent, payload, session, worker=wrong)
    assert err.value.status_code == 401
    right = service_auth.token_for(service_auth.meeting_scope("abcd-efgh"))
    config = _resolve(agent, payload, session, worker=right)
    assert config["workspace_id"] == "theirs"          # the meeting's, never the body's


def test_a_repo_id_from_another_workspace_is_refused(monkeypatch, worker_key):
    from app.models import Meeting, Repo

    agent, _ = _ask_env(monkeypatch)
    session = _AskSession(meeting=Meeting(slug="abcd-efgh", workspace_id="theirs"),
                          repos={"r-other": Repo(id="r-other", name="x", source_type="github",
                                                 workspace_id="victim")})
    payload = agent.AgentAskRequest(question="q", meeting_slug="abcd-efgh", repo_id="r-other")
    token = service_auth.token_for(service_auth.meeting_scope("abcd-efgh"))
    with pytest.raises(HTTPException) as err:
        _resolve(agent, payload, session, worker=token)
    assert err.value.status_code == 404
