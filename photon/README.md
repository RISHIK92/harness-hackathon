# Photon

**Company brain + live call support agent.** Photon indexes a company's
code, docs, tickets, Slack, Jira and other sources, then answers support
questions — over a web console or a real live voice call — grounded
entirely in cited evidence. It never guesses: every claim carries an
`[ev_xxx]` citation back to a real source, and it abstains rather than
answer from nothing.

## What it does

- **Ingests your own sources**, workspace by workspace: GitHub repos
  (via a GitHub App — public or private orgs), Slack, Jira, and a
  generic Linear/Notion/Datadog connector, plus pasted/uploaded custom
  docs. Nothing is indexed until you explicitly select it.
- **Answers grounded questions** through an agent loop that plans which
  tools to call (`search_code`, `search_slack`, `explain_why`,
  `get_account_logs`, …), executes them, composes an answer, then
  verifies every claim actually traces back to real evidence before it's
  shown — a claim that fails verification is stripped, not shown.
- **Joins live calls** over LiveKit: real-time STT/TTS (Deepgram by
  default, Sarvam for Indic languages), a waiting room for guests, poke-
  to-address on multi-party calls, screen-share vision, and a live
  "advanced" panel showing the pipeline (plan → tools → compose → verify)
  as it runs.
- **Multi-tenant from day one**: JWT/GitHub-OAuth login, workspaces with
  invites and roles (owner/member/viewer), and every tool/query scoped to
  the calling workspace.

## Architecture

```
client/       Next.js console — login, dashboard, sources, live call UI
server/       FastAPI "brain" — agent loop, tools, ingestion, all APIs
call-agent/   LiveKit voice worker — STT/TTS, orchestrates calls against server/
```

`call-agent/` and `server/` communicate over plain HTTP only
(`POST /api/agent/ask(/stream)`); `server/app/agent/` and
`server/app/tools/` contain **zero transport imports** — the agent loop
is callable from a script exactly as it is from a live call. `call-agent/`
has its own Python virtualenv, deliberately separate from `server/`'s
(see [Setup](#setup) — a dependency conflict otherwise).

### Data stores (via `docker-compose.yml`)

| Store | Used for |
|---|---|
| PostgreSQL | Users, workspaces, repos, meetings, connections |
| Qdrant | Vector search — code chunks, docs, tickets, Slack, connector items |
| Neo4j | Code structure graph (symbols, imports, call sites) |
| Redis | Celery broker (ingestion/sync jobs) + pub/sub (job progress) |

## Repo layout

```
client/app/
  login/                Sign in / sign up, GitHub OAuth
  dashboard/             Workspace switcher, sources, repo list, members
  call/                  Live call UI — video, captions, evidence panel, trace panel
  api/livekit-token/     Server-side LiveKit token minting

server/app/
  agent/                 loop.py, prompts.py, verifier.py — the planning/answer loop
  tools/                 search_code, search_slack, explain_why, get_account, …
  routers/               auth, workspaces, repos, meetings, agent, connectors, mock, …
  services/connectors/   Shared Linear/Notion/Datadog connector implementation
  mock/                  "Mock" testing data (dashboard button) — see below
  seed/                  Fictional "Meridian" demo corpus, used by the eval harness

call-agent/
  orchestrator.py        Turn handling, transcript, screen-frame buffering
  adapters/               TransportAdapter contract + the LiveKit implementation
  worker.py               Entry point (`python3 worker.py dev`)
```

## Setup

**Prerequisites**: Docker, Python 3.12 (not 3.13 — `voyageai` has no 3.13
wheel), Node 18+.

```bash
# 1. Data stores
docker compose up -d

# 2. Server (brain-api)
cd server
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install greenlet   # transitive dep of the async SQLAlchemy engine, not pinned
cp .env.example .env   # fill in the keys below
uvicorn app.main:app --reload --port 8000

# 3. Celery worker (ingestion/sync jobs) — separate terminal, same venv
cd server && source .venv/bin/activate
celery -A app.tasks.celery_app worker --loglevel=info
# macOS note: use --pool=solo. The default forking pool can segfault
# (an Objective-C fork-safety crash in a native library) the first time
# it parses a TypeScript file during ingestion.

# 4. Client (console)
cd client
npm install
cp .env.local.example .env.local
npm run dev   # http://localhost:3000

# 5. Call agent (voice worker) — only needed for live calls
cd call-agent
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 worker.py dev
```

### Environment variables

**`server/.env`**

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | JWT signing + Fernet key for encrypted connector credentials |
| `POSTGRES_*` / `REDIS_URL` / `QDRANT_HOST` / `QDRANT_PORT` | Data store connections |
| `GEMINI_API_KEY`, `VOYAGE_API_KEY` | Embeddings |
| `OPENROUTER_API_KEY` | All text/vision LLM calls (plan, compose, vision) — see note below |
| `GITHUB_TOKEN` | Fallback token for public-repo cloning |
| `GITHUB_APP_ID` / `GITHUB_APP_SLUG` / `GITHUB_APP_CLIENT_ID` / `GITHUB_APP_CLIENT_SECRET` / `GITHUB_APP_PRIVATE_KEY` | GitHub App (sign-in + private-org repo access) — bootstrap via `/dev/github-app/new` (dev-only route) |
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | LiveKit Cloud project |
| `DEEPGRAM_API_KEY`, `SARVAM_API_KEY` | Voice stack credentials (used by `call-agent/`, not the server itself) |
| `REPOS_STORAGE_PATH` | Local clone storage — set this; the default gets wiped on reboot |

> **Why OpenRouter for everything, including vision**: Gemini's free tier
> caps at 20 requests/day per model — a hard wall, not a rate limit that
> retries can wait out. All text and vision calls route through
> OpenRouter instead; `GEMINI_API_KEY` is kept only for the embedding
> model, which isn't subject to the same cap.

**`client/.env.local`**

| Variable | Purpose |
|---|---|
| `NEXT_PUBLIC_BRAIN_API_URL` | Where the console calls the server |
| `NEXT_PUBLIC_LIVEKIT_URL` | Public LiveKit URL (browser-side) |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | Server-side only — the token-minting route needs these; never sent to the browser |

**`call-agent/.env`**

| Variable | Purpose |
|---|---|
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | Same LiveKit project as the server |
| `BRAIN_API_URL` | Where the voice worker calls the server |
| `DEEPGRAM_API_KEY` | Default voice stack (STT: nova-3, TTS: aura-2) |
| `VOICE_STACK` | `deepgram` (default) or `sarvam` — switches STT **and** TTS together |
| `SARVAM_API_KEY` / `SARVAM_TTS_MODEL` / `SARVAM_TTS_SPEAKER` / `SARVAM_STT_MODEL` | Only used when `VOICE_STACK=sarvam` (Telugu/Tamil/Hindi/English) |

## Testing

```bash
cd server && .venv/bin/pytest tests/ -q
cd call-agent && .venv/bin/pytest tests/ -q
cd client && npx tsc --noEmit

# Agent accuracy eval — run before trusting any change to the model,
# prompts, or tool-selection guidance:
cd server && .venv/bin/python evals/agent_eval.py <model> [trials]
HARD=1 .venv/bin/python evals/agent_eval.py <model> [trials]
```

## Trying it without connecting anything real

The dashboard's **Mock** button (per source, and on the call setup
screen) indexes small, clearly-labeled `[MOCK]` sample data — modeled on
a real exam-prep backend — so a brand-new workspace has something to ask
about immediately. It's indexed through the exact same code path a real
connection uses, is never mistaken for one (every item is prefixed
`[MOCK]`, the mock repo is named accordingly), and can be turned off
per-source at any time.

## Deployment

- **`client/`** deploys to Vercel (root directory `client`), with the
  four `NEXT_PUBLIC_*`/`LIVEKIT_*` variables above set as project env vars.
- **`server/`** and **`call-agent/`** are long-running processes (FastAPI
  + Celery worker + LiveKit worker) — they need a host that supports
  that, not a serverless platform. `call-agent/` in particular must stay
  connected to LiveKit Cloud for the duration of any call.
- `server/app/routers/dev_github_setup.py` and `dev_slack_setup.py` are
  **dev-only bootstrap routes** (App/manifest creation) — mounted only
  outside production and never linked from the product UI.

## Scope notes

- `POST /api/agent/ask(/stream)` is unauthenticated by design (documented
  demo-scope decision) — `workspace_id` on that route is client-asserted,
  the same trust boundary as the rest of that endpoint today.
- The seed `Meridian` corpus (`server/app/seed/`) is a fictional company
  used only by the eval harness and gated behind `enable_demo_corpus`
  (off by default) — it never appears in a real workspace.
- No webhook support for GitHub yet — the repo picker computes the
  new-vs-already-imported diff live against GitHub's API on every open
  instead.
