from __future__ import annotations
import asyncio
import json
import structlog
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.config import get_settings, validate_security
from app.core.repo_access import require_repo_access
from app.database import create_db_and_tables
from app.models import User, LearningPathCache  # ensure tables are registered before create_all
from app.routers import repos, jobs, graph, annotations, files, workspaces, meetings
from app.routers import auth
from app.routers import onboarding
from app.routers import tools
from app.routers import agent
from app.routers import github_app
from app.routers import slack as slack_router
from app.routers import jira as jira_router
from app.routers import connectors as connectors_router
from app.routers import custom_docs as custom_docs_router
from app.routers import mock as mock_router
from app.routers import whisper as whisper_router
from app.routers import agent_jobs as agent_jobs_router
from app.routers import escalations as escalations_router
from app.routers import extension as extension_router
from app.routers import admin as admin_router
from app.routers import agent_worker as agent_worker_router
from app.routers import dev_github_setup
from app.routers import dev_slack_setup
from app.routers import dev_ask

log = structlog.get_logger()
settings = get_settings()
# Before anything is served: a published default secret signs anyone's
# session and decrypts every stored connector token. Refuses to import (so
# uvicorn refuses to start) outside APP_ENV=development.
validate_security(settings)

# ─── WebSocket connection manager ─────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, repo_id: str, ws: WebSocket):
        await ws.accept()
        self._connections.setdefault(repo_id, []).append(ws)

    def disconnect(self, repo_id: str, ws: WebSocket):
        if repo_id in self._connections:
            self._connections[repo_id].discard(ws) if hasattr(
                self._connections[repo_id], "discard"
            ) else None
            try:
                self._connections[repo_id].remove(ws)
            except ValueError:
                pass

    async def broadcast(self, repo_id: str, message: dict):
        dead = []
        for ws in self._connections.get(repo_id, []):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(repo_id, ws)


manager = ConnectionManager()


# ─── Redis pub/sub listener ───────────────────────────────────────────────────

async def redis_listener():
    """Subscribe to job progress events published by Celery workers and
    forward them to the correct WebSocket connections."""
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    pubsub = redis.pubsub()
    await pubsub.psubscribe("job:*")
    log.info("Redis pub/sub listener started")
    async for message in pubsub.listen():
        if message["type"] != "pmessage":
            continue
        try:
            data = json.loads(message["data"])
            repo_id = data.get("repo_id")
            if repo_id:
                await manager.broadcast(repo_id, data)
        except Exception as exc:
            log.warning("redis_listener.parse_error", error=str(exc))


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_db_and_tables()
    task = asyncio.create_task(redis_listener())
    log.info("YASML API started")
    yield
    task.cancel()
    log.info("YASML API stopped")


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="YASML — Codebase Intelligence API",
    description="Ingest, graph, and query any codebase with natural language.",
    version="0.1.0",
    lifespan=lifespan,
)

@app.middleware("http")
async def errors_as_json(request, call_next):
    """Turn an unhandled crash into a JSON 500 the browser can actually read.

    Registered BEFORE the CORS middleware so it sits inside it: Starlette's
    own last-resort 500 is produced outside CORS, carries no
    Access-Control-Allow-Origin header, and the browser therefore reports
    every server crash as "Failed to fetch" — indistinguishable from the API
    being down. Here the response goes back out through CORS like any other.
    """
    from fastapi.responses import JSONResponse

    try:
        return await call_next(request)
    except Exception as exc:  # noqa: BLE001
        log.exception("api.unhandled", path=request.url.path)
        detail = f"The server hit an error ({type(exc).__name__}) — the API log has the details."
        # The one crash a fresh deployment hits constantly: no embedding key,
        # so anything that indexes (mock data, uploads, syncs) fails.
        if type(exc).__module__.startswith("voyageai") and "API key" in str(exc):
            return JSONResponse(status_code=503, content={
                "detail": "Indexing needs an embeddings key — set VOYAGE_API_KEY in server/.env and restart the API."})
        return JSONResponse(status_code=500, content={"detail": detail})


app.add_middleware(
    CORSMiddleware,
    # Explicit origins (CORS_ORIGINS, else CLIENT_BASE_URL). "*" together
    # with credentials let any site script this API from a signed-in
    # browser the moment cookies are involved.
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routers ──────────────────────────────────────────────────────────────────

# The original codebase-intelligence routers address a repo (or its job or
# pin) by id in the URL and used to serve it to anyone. require_repo_access
# resolves that repo and 404s unless the caller's workspace owns it.
# /api/query is gone: it duplicated the agent without citations and, with a
# blank repo_id, searched every tenant's code.
_repo_scoped = [Depends(require_repo_access)]
app.include_router(repos.router,        prefix="/api/repos",       tags=["repos"])
app.include_router(jobs.router,         prefix="/api/jobs",        tags=["jobs"], dependencies=_repo_scoped)
app.include_router(graph.router,        prefix="/api/graph",       tags=["graph"], dependencies=_repo_scoped)
app.include_router(annotations.router,  prefix="/api/annotations", tags=["annotations"], dependencies=_repo_scoped)
app.include_router(files.router,        prefix="/api/files",       tags=["files"], dependencies=_repo_scoped)
app.include_router(auth.router,         prefix="/api/auth",        tags=["auth"])
app.include_router(workspaces.router,   prefix="/api/workspaces",  tags=["workspaces"])
app.include_router(meetings.router,     prefix="/api/meetings",    tags=["meetings"])
app.include_router(onboarding.router,   prefix="/api/repos",       tags=["onboarding"], dependencies=_repo_scoped)
app.include_router(tools.router,        prefix="/api/tools",       tags=["tools"])
# A signed-in member or the call-agent worker (meeting-scoped token); the
# tenant comes from that principal, never from the request body.
app.include_router(agent.router,        prefix="/api/agent",       tags=["agent"])
app.include_router(github_app.router,   prefix="/api/integrations/github", tags=["github"])
app.include_router(slack_router.router, prefix="/api/integrations/slack",  tags=["slack"])
app.include_router(jira_router.router,  prefix="/api/integrations/jira",   tags=["jira"])
app.include_router(connectors_router.router, prefix="/api/integrations/connectors", tags=["connectors"])
app.include_router(custom_docs_router.router, prefix="/api/custom-docs", tags=["custom-docs"])
app.include_router(mock_router.router, prefix="/api/mock", tags=["mock"])
app.include_router(whisper_router.router, prefix="/api/whisper", tags=["whisper"])
app.include_router(agent_jobs_router.router, prefix="/api/agent-jobs", tags=["agent-jobs"])
app.include_router(agent_jobs_router.webhook_router, prefix="/api/integrations/github", tags=["github"])
app.include_router(agent_jobs_router.linear_router, prefix="/api/integrations/linear", tags=["linear"])
app.include_router(escalations_router.router, prefix="/api/escalations", tags=["escalations"])
app.include_router(extension_router.router, prefix="/api/extension", tags=["extension"])
app.include_router(admin_router.router, prefix="/api/admin", tags=["admin"])
app.include_router(agent_worker_router.router, prefix="/api/agent-worker", tags=["agent-worker"])

# Dev-only GitHub App manifest bootstrap — never linked from product UI.
# Mounted only under an explicit APP_ENV=development (the default is
# production, so a deployment that forgets to set it gets none of this).
if settings.app_env == "development":
    app.include_router(dev_github_setup.router, prefix="/dev", tags=["dev"])
    app.include_router(dev_slack_setup.router, prefix="/dev", tags=["dev"])
    # Impersonates a user with no token — see the module docstring. Needs a
    # second, specific opt-in on top of development: it is the one route that
    # answers as someone else, and a development box can still be exposed
    # (scripts/dev.sh --with-ngrok publishes :8000).
    if settings.enable_dev_impersonation:
        app.include_router(dev_ask.router, prefix="/dev", tags=["dev"])


# ─── WebSocket endpoint ───────────────────────────────────────────────────────

@app.websocket("/ws/{repo_id}")
async def websocket_endpoint(websocket: WebSocket, repo_id: str):
    # Ingestion progress for one repo. Browsers cannot set headers on a
    # WebSocket, so the session token rides in ?token=; the repo must belong
    # to a workspace that user is a member of.
    if not await _websocket_may_watch(websocket.query_params.get("token"), repo_id):
        await websocket.close(code=4404)
        return
    await manager.connect(repo_id, websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive pings
    except WebSocketDisconnect:
        manager.disconnect(repo_id, websocket)


async def _websocket_may_watch(token: str | None, repo_id: str) -> bool:
    from jose import JWTError, jwt

    from app.core.workspace import membership_for
    from app.database import AsyncSessionLocal
    from app.models import Repo

    if not token:
        return False
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return False
    user_id = payload.get("sub")
    if not user_id or payload.get("scope"):
        return False
    async with AsyncSessionLocal() as session:
        repo = await session.get(Repo, repo_id)
        if repo is None:
            return False
        if repo.workspace_id:
            return await membership_for(session, repo.workspace_id, user_id) is not None
        return repo.owner_id == user_id


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
