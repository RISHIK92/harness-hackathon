#!/usr/bin/env bash
#
# Bring the whole Photon stack up (or down) in one go.
#
#   ./scripts/dev.sh                 start everything except ngrok
#   ./scripts/dev.sh --with-ngrok    ...and expose the brain-api publicly
#   ./scripts/dev.sh stop            stop everything this script starts
#   ./scripts/dev.sh restart         stop, then start
#   ./scripts/dev.sh status          what is up, and is it actually working
#   ./scripts/dev.sh logs <name>     tail one service's log
#   ./scripts/dev.sh setup           create the venvs and node_modules (once)
#
# Design notes, each one paid for by a real debugging session:
#
#   * Readiness is checked by TALKING to a service, never by `sleep`. A
#     process that has started is not a process that works — most of the
#     time lost on this stack has gone to something that was "running" and
#     silently broken.
#
#   * Celery is ALWAYS restarted, even if already running. It has no
#     --reload, so a worker outlives every edit to app/tasks/. A worker
#     running code from before a change once indexed a whole repo without
#     its workspace tag and made every multi-repo search return nothing,
#     with no error anywhere. Restarting is cheap; that bug was not.
#
#   * Qdrant's container health is ignored. Its docker healthcheck sits at
#     "starting"/"unhealthy" forever while the service answers HTTP 200
#     perfectly well, so this polls the API instead of the health flag.
#
#   * server/ and call-agent/ have SEPARATE venvs and must stay that way:
#     livekit-api force-upgrades protobuf to 7.x, which breaks the
#     embedding/Gemini stack in server/.venv. Never cross the interpreters.
#
#   * ngrok is opt-in. Its free URL changes on every restart, and that URL
#     is baked into Vercel as NEXT_PUBLIC_BRAIN_API_URL, so an unthinking
#     restart silently points the deployed frontend at a dead tunnel.
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS=/tmp
cd "$ROOT"

RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; DIM=$'\033[2m'; OFF=$'\033[0m'
ok()   { printf "  ${GRN}✓${OFF} %s\n" "$*"; }
warn() { printf "  ${YEL}!${OFF} %s\n" "$*"; }
bad()  { printf "  ${RED}✗${OFF} %s\n" "$*"; }
step() { printf "\n${DIM}%s${OFF}\n" "$*"; }

# Match on the exact command line so we never kill an unrelated process that
# happens to share a word.
PAT_API="uvicorn app.main:app"
PAT_CELERY="celery -A app.tasks.celery_app worker"
PAT_NEXT="next dev"
PAT_AGENT="worker.py dev"
PAT_NGROK="ngrok http 8000"
PAT_HARNESS="harness.service"

running() { pgrep -f "$1" >/dev/null 2>&1; }

# Running FROM THIS CHECKOUT, not just running. The patterns above also match
# the same service started from another copy of this repo on the machine,
# which is how "already running" once hid that the only call-agent up was an
# older copy that never answers this stack's calls.
running_here() { # pattern, dir
  local pid
  for pid in $(pgrep -f "$1" 2>/dev/null); do
    [ "$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')" = "$2" ] && return 0
  done
  return 1
}

# A missing virtualenv used to surface as "see <log>" after a timeout. Say it
# up front, with the command that fixes it.
need_venv() { # dir
  [ -x "$ROOT/$1/.venv/bin/python" ] && return 0
  bad "$1 has no virtualenv — run: ./scripts/dev.sh setup"
  return 1
}

# Poll a command until it succeeds, bounded by WALL-CLOCK time.
#
# The obvious version — loop N times with a 1s sleep — silently lies about
# its own timeout, because it assumes the probe returns instantly. It does
# not: `celery inspect ping` costs ~2.6s per call here, and a curl against a
# cold Next.js dev server blocks for its full --max-time while the first
# request compiles. A "90s" timeout built that way ran for over seven
# minutes before anyone noticed it had not hung — it was still counting.
#
# So the deadline is a timestamp, checked after each attempt.
wait_for() {
  local label="$1" timeout="$2"; shift 2
  local deadline=$(( SECONDS + timeout ))
  while :; do
    if "$@" >/dev/null 2>&1; then ok "$label"; return 0; fi
    [ "$SECONDS" -ge "$deadline" ] && break
    sleep 1
  done
  bad "$label — not ready after ${timeout}s"
  return 1
}

# Celery's own client will wait indefinitely for a broker that never answers,
# so it gets an explicit timeout rather than relying on the loop above to cut
# it off — the loop can only act between attempts, not during one.
# The output is CAPTURED and then matched, never piped into `grep -q`.
# Under `set -o pipefail` that pipeline reports failure for a perfectly
# healthy worker: grep -q exits the instant it matches, celery takes SIGPIPE
# writing the rest, and pipefail surfaces celery's 141 as the pipeline's
# status. It reproduces in bash and NOT in zsh, so checking it by hand at an
# interactive prompt says everything is fine while the script says it is
# broken.
celery_ping() {
  local out
  out=$( cd "$ROOT/server" && .venv/bin/celery -A app.tasks.celery_app inspect ping --timeout 3 2>/dev/null )
  [[ "$out" == *pong* ]]
}

http() { curl -sf -o /dev/null --max-time 5 "$1"; }

# Redis is probed on the PORT, not through `docker compose exec`, because the
# port is what the app actually connects to and the two can disagree. On this
# machine a standalone `my-redis` container (no compose project, created back
# in July) squats :6379, so compose's own redis can never bind and stays down
# — while redis on :6379 answers PONG perfectly well and the app is fine. A
# container-scoped check would report a broken stack that is not broken.
redis_up() {
  python3 -c 'import socket,sys
try:
    s = socket.create_connection(("localhost", 6379), 2)
    s.sendall(b"PING\r\n")
    sys.exit(0 if b"PONG" in s.recv(64) else 1)
except Exception:
    sys.exit(1)' >/dev/null 2>&1
}

start_containers() {
  step "docker — postgres, redis, neo4j, qdrant"
  # The exit code is NOT the signal. `docker compose up -d` returns non-zero
  # for a port collision on ONE service even when everything the stack needs
  # is serving, and returns zero for containers that are up but not yet
  # accepting connections. Both were observed here. So: run it, keep stderr
  # for diagnosis, and let the probes decide — the same rule as the rest of
  # this script.
  local err
  err=$(docker compose up -d 2>&1 >/dev/null)

  wait_for "postgres"  60 docker compose exec -T postgres pg_isready -U yasml -d yasml
  wait_for "redis"     30 redis_up
  wait_for "neo4j"     90 curl -sf -o /dev/null --max-time 5 http://localhost:7474
  # Deliberately the HTTP API, not the container health flag — see header.
  wait_for "qdrant"    60 curl -sf -o /dev/null --max-time 5 http://localhost:6333/collections

  # Only worth raising once everything that matters is confirmed serving.
  if grep -q "port is already allocated" <<<"$err"; then
    local svc
    svc=$(grep -o "photon-[a-z0-9]*-1" <<<"$err" | head -1)
    warn "compose could not bind a port for ${svc:-a container} — another"
    warn "container already owns it. Harmless while the probes above pass:"
    warn "something IS serving that port. See: docker ps --filter publish=<port>"
  fi
}

start_api() {
  step "brain-api :8000"
  need_venv server || return 0
  if running "$PAT_API"; then
    http http://localhost:8000/health && { ok "already running"; return 0; }
    warn "running but not answering /health — restarting"
    pkill -f "$PAT_API"; sleep 2; pkill -9 -f "$PAT_API" 2>/dev/null; sleep 1
  fi
  ( cd "$ROOT/server" && nohup .venv/bin/uvicorn app.main:app --reload --port 8000 \
      > "$LOGS/uvicorn.log" 2>&1 & )
  wait_for "healthy" 60 curl -sf -o /dev/null --max-time 5 http://localhost:8000/health \
    || warn "see $LOGS/uvicorn.log"
}

start_celery() {
  # Always restarted — see the header. This is the point of the script.
  step "celery worker ${DIM}(always restarted: no --reload)${OFF}"
  need_venv server || return 0
  if running "$PAT_CELERY"; then
    pkill -f "$PAT_CELERY"; sleep 3; pkill -9 -f "$PAT_CELERY" 2>/dev/null; sleep 1
  fi
  # macOS: no forking pool. The prefork child is killed the moment it runs a
  # task — SIGABRT from the Objective-C fork-safety check ("+[NSCharacterSet
  # initialize] may have been in progress in another thread when fork() was
  # called"), and with that silenced, SIGSEGV from a native library instead.
  # Either way the task is lost and the repo sits at "Indexing" forever with
  # no error in the UI. Measured: the same ingest under --pool=solo finishes
  # in ~13s. Solo runs one task at a time, which costs little — concurrent
  # imports contend on the embedding API anyway (see CLAUDE.md, ingest speed).
  local pool=()
  [ "$(uname -s)" = "Darwin" ] && pool=(--pool=solo)
  ( cd "$ROOT/server" && OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES \
      nohup .venv/bin/celery -A app.tasks.celery_app worker --loglevel=info "${pool[@]}" \
      > "$LOGS/celery.log" 2>&1 & )
  # `inspect ping` is the only honest check: the process can be alive while
  # the app failed to import, which is exactly how a broken task module hides.
  wait_for "responding to ping" 60 celery_ping \
    || warn "see $LOGS/celery.log — an ImportError in app/tasks/ stops the whole worker"
}

start_client() {
  step "next dev :3000"
  if running "$PAT_NEXT"; then ok "already running"; return 0; fi
  ( cd "$ROOT/client" && nohup npm run dev > "$LOGS/nextdev.log" 2>&1 & )
  wait_for "serving" 120 curl -sf -o /dev/null --max-time 5 http://localhost:3000 \
    || warn "see $LOGS/nextdev.log"
}

start_agent() {
  step "call-agent worker ${DIM}(livekit)${OFF}"
  need_venv call-agent || return 0
  if running_here "$PAT_AGENT" "$ROOT/call-agent"; then ok "already running"; return 0; fi
  if running "$PAT_AGENT"; then
    # Another copy's worker is up: it registers under a different (or no)
    # agent name, so it would never be dispatched to this stack's calls.
    warn "a call-agent from another checkout is running — it won't join this stack's calls"
  fi
  ( cd "$ROOT/call-agent" && nohup .venv/bin/python worker.py dev \
      > "$LOGS/callagent.log" 2>&1 & )
  wait_for "registered with livekit" 45 \
    grep -q "registered worker" "$LOGS/callagent.log" \
    || warn "see $LOGS/callagent.log"
}

# The harness lives one level up and is started unchanged: agent jobs call it
# over HTTP. Its key and token come from server/.env so there is one place to
# set them — HARNESS_SERVICE_TOKEN must be the same value on both sides.
HARNESS_ROOT="$(cd "$ROOT/.." && pwd)"
env_value() { grep -E "^$1=" "$ROOT/server/.env" 2>/dev/null | tail -1 | cut -d= -f2- | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//'; }

start_harness() {
  step "harness service :8765"
  if [ ! -d "$HARNESS_ROOT/harness" ]; then warn "no harness at $HARNESS_ROOT — agent jobs will fail"; return 0; fi
  if running "$PAT_HARNESS"; then ok "already running"; return 0; fi
  local py="$HARNESS_ROOT/.venv/bin/python"
  [ -x "$py" ] || py=python3
  local key="${AI_API_KEY:-$(env_value AI_API_KEY)}"
  [ -n "$key" ] || warn "AI_API_KEY is not set (env or server/.env) — every run will exit CONFIG_ERROR"
  # HARNESS_SERVICE_ALLOW_LOCAL: the Mock button's repo is a local path, and
  # the service refuses anything but https URLs unless told otherwise. Only
  # here, for local development: the service binds to loopback when it has
  # no token, and --with-ngrok exposes :8000, never :8765.
  ( cd "$HARNESS_ROOT" && AI_API_KEY="$key" \
      HARNESS_SERVICE_TOKEN="${HARNESS_SERVICE_TOKEN:-$(env_value HARNESS_SERVICE_TOKEN)}" \
      HARNESS_SERVICE_ALLOW_LOCAL=1 \
      nohup "$py" -m harness.service > "$LOGS/harness.log" 2>&1 & )
  wait_for "healthy" 20 curl -sf -o /dev/null --max-time 5 http://127.0.0.1:8765/v1/health \
    || warn "see $LOGS/harness.log"
}

start_ngrok() {
  step "ngrok → :8000"
  if running "$PAT_NGROK"; then ok "already running"; else
    nohup ngrok http 8000 --log=stdout > "$LOGS/ngrok.log" 2>&1 &
    wait_for "tunnel open" 30 curl -sf -o /dev/null --max-time 5 http://localhost:4040/api/tunnels || return 1
  fi
  local url
  url=$(curl -s --max-time 5 http://localhost:4040/api/tunnels \
        | python3 -c 'import sys,json;t=json.load(sys.stdin)["tunnels"];print(t[0]["public_url"] if t else "")' 2>/dev/null)
  [ -n "$url" ] && printf "    %s\n" "$url"
  warn "free-tier URLs change on every restart — if this differs from Vercel's"
  warn "NEXT_PUBLIC_BRAIN_API_URL, re-push it and redeploy or the site is dead"
}

do_start() {
  local with_ngrok="$1"
  start_containers || return 1
  start_api
  start_celery
  start_client
  start_agent
  start_harness
  [ "$with_ngrok" = "yes" ] && start_ngrok
  do_status
}

do_stop() {
  step "stopping processes"
  for pat in "$PAT_NEXT" "$PAT_API" "$PAT_CELERY" "$PAT_AGENT" "$PAT_HARNESS" "$PAT_NGROK"; do
    if running "$pat"; then
      pkill -f "$pat" 2>/dev/null; sleep 2
      # uvicorn has ignored SIGTERM here before and stayed bound to :8000.
      running "$pat" && pkill -9 -f "$pat" 2>/dev/null
      ok "${pat%% *}"
    fi
  done
  step "stopping containers ${DIM}(stop, not down — volumes keep your data)${OFF}"
  docker compose stop >/dev/null 2>&1 && ok "postgres, redis, neo4j, qdrant"
  for port in 3000 8000; do
    lsof -ti:"$port" >/dev/null 2>&1 && bad "port $port still bound" || ok "port $port free"
  done
}

check() { # label, test-command...
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$label"; else bad "$label"; fi
}

do_status() {
  step "status"
  check "postgres"        docker compose exec -T postgres pg_isready -U yasml -d yasml
  check "redis    :6379"  redis_up
  check "neo4j    :7474"  curl -sf -o /dev/null --max-time 5 http://localhost:7474
  check "qdrant   :6333"  curl -sf -o /dev/null --max-time 5 http://localhost:6333/collections
  check "brain-api:8000"  curl -sf -o /dev/null --max-time 5 http://localhost:8000/health
  check "next dev :3000"  curl -sf -o /dev/null --max-time 5 http://localhost:3000
  check "celery"          celery_ping
  if running "$PAT_AGENT"; then ok "call-agent"; else bad "call-agent"; fi
  check "harness  :8765"  curl -sf -o /dev/null --max-time 5 http://127.0.0.1:8765/v1/health
  if running "$PAT_NGROK"; then
    printf "  ${GRN}✓${OFF} ngrok      %s\n" \
      "$(curl -s --max-time 5 http://localhost:4040/api/tunnels | python3 -c 'import sys,json;t=json.load(sys.stdin)["tunnels"];print(t[0]["public_url"] if t else "?")' 2>/dev/null)"
  else
    printf "  ${DIM}-${OFF} ngrok      not running (start with --with-ngrok)\n"
  fi
  echo
}

do_logs() {
  case "${1:-}" in
    api|uvicorn) tail -f "$LOGS/uvicorn.log" ;;
    celery)      tail -f "$LOGS/celery.log" ;;
    client|next) tail -f "$LOGS/nextdev.log" ;;
    agent)       tail -f "$LOGS/callagent.log" ;;
    ngrok)       tail -f "$LOGS/ngrok.log" ;;
    harness)     tail -f "$LOGS/harness.log" ;;
    *) echo "usage: $0 logs {api|celery|client|agent|harness|ngrok}"; exit 1 ;;
  esac
}

# One-time: the virtualenvs and node_modules a fresh checkout does not have.
# server/ and call-agent/ get SEPARATE venvs on purpose (see the header).
do_setup() {
  local py
  py=$(command -v python3.12 || true)
  [ -n "$py" ] || { bad "python3.12 not found — voyageai has no 3.13 wheel; install 3.12 first"; return 1; }
  step "server/.venv"
  if [ ! -x "$ROOT/server/.venv/bin/python" ]; then
    "$py" -m venv "$ROOT/server/.venv" && ok "created"
  fi
  "$ROOT/server/.venv/bin/pip" install -q -r "$ROOT/server/requirements.txt" greenlet && ok "dependencies installed"
  step "call-agent/.venv"
  if [ ! -x "$ROOT/call-agent/.venv/bin/python" ]; then
    "$py" -m venv "$ROOT/call-agent/.venv" && ok "created"
  fi
  "$ROOT/call-agent/.venv/bin/pip" install -q -r "$ROOT/call-agent/requirements.txt" && ok "dependencies installed"
  step "client/node_modules"
  ( cd "$ROOT/client" && npm install --silent ) && ok "installed"
  for f in server/.env client/.env.local call-agent/.env; do
    [ -e "$ROOT/$f" ] || warn "$f is missing — copy it from $f.example (or the matching .example) and fill in the keys"
  done
}

WITH_NGROK=no
CMD=start
for arg in "$@"; do
  case "$arg" in
    --with-ngrok) WITH_NGROK=yes ;;
    start|stop|restart|status|logs|setup) CMD="$arg" ;;
    *) [ "$CMD" = "logs" ] && LOG_TARGET="$arg" ;;
  esac
done

case "$CMD" in
  setup)   do_setup ;;
  start)   do_start "$WITH_NGROK" ;;
  stop)    do_stop ;;
  restart) do_stop; do_start "$WITH_NGROK" ;;
  status)  do_status ;;
  logs)    do_logs "${LOG_TARGET:-}" ;;
esac
