from __future__ import annotations
import asyncio
import json
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.config import get_settings
from app.database import create_db_and_tables
from app.models import User, LearningPathCache  # ensure tables are registered before create_all
from app.routers import repos, jobs, query, graph, annotations, files, workspaces, meetings
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
    allow_origins=["*"],  # Allows all origins (perfect for hackathons)
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods (GET, POST, PUT, DELETE, OPTIONS)
    allow_headers=["*"],  # Allows all headers
)

# ─── Routers ──────────────────────────────────────────────────────────────────

app.include_router(repos.router,        prefix="/api/repos",       tags=["repos"])
app.include_router(jobs.router,         prefix="/api/jobs",        tags=["jobs"])
app.include_router(query.router,        prefix="/api/query",       tags=["query"])
app.include_router(graph.router,        prefix="/api/graph",       tags=["graph"])
app.include_router(annotations.router,  prefix="/api/annotations", tags=["annotations"])
app.include_router(files.router,        prefix="/api/files",       tags=["files"])
app.include_router(auth.router,         prefix="/api/auth",        tags=["auth"])
app.include_router(workspaces.router,   prefix="/api/workspaces",  tags=["workspaces"])
app.include_router(meetings.router,     prefix="/api/meetings",    tags=["meetings"])
app.include_router(onboarding.router,   prefix="/api/repos",       tags=["onboarding"])
app.include_router(tools.router,        prefix="/api/tools",       tags=["tools"])  # unauthenticated for the demo, see CLAUDE.md
app.include_router(agent.router,        prefix="/api/agent",       tags=["agent"])  # unauthenticated for the demo, see CLAUDE.md
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

# Dev-only GitHub App manifest bootstrap — never linked from product UI,
# not mounted in production. See app/routers/dev_github_setup.py.
if settings.app_env != "production":
    app.include_router(dev_github_setup.router, prefix="/dev", tags=["dev"])
    app.include_router(dev_slack_setup.router, prefix="/dev", tags=["dev"])
    # Impersonates a user with no token — see the module docstring. Gated
    # by this same app_env check, which is the only thing keeping it off a
    # deployment that holds anyone else's data.
    app.include_router(dev_ask.router, prefix="/dev", tags=["dev"])


# ─── WebSocket endpoint ───────────────────────────────────────────────────────

@app.websocket("/ws/{repo_id}")
async def websocket_endpoint(websocket: WebSocket, repo_id: str):
    await manager.connect(repo_id, websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive pings
    except WebSocketDisconnect:
        manager.disconnect(repo_id, websocket)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
