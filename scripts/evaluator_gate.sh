#!/usr/bin/env bash
# The evaluator's exact path:  make setup  then  make run ISSUE="<issue url>"
#
# A local stub stands in for api.github.com (via GITHUB_API_URL) and a local
# bare repository stands in for the origin, so the whole GitHub route -- URL
# parse, issue fetch with comments, clone, branch, fix, verify, report -- runs
# for real without a network or a token.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
PASS=0; FAIL=0
step() { printf "\n== %s\n" "$1"; }
ok()   { printf "   ok\n"; PASS=$((PASS+1)); }
bad()  { printf "   FAIL %s\n" "${1:-}"; FAIL=$((FAIL+1)); }
# The work directory is kept: when this gate fails, the run log is the
# only thing that says why, and deleting it was how the first failure
# became unreadable.
cleanup() { [ -n "${STUB_PID:-}" ] && kill "$STUB_PID" 2>/dev/null; true; }
trap cleanup EXIT

step "build an upstream repository with a failing test"
mkdir -p "$WORK/upstream/src/lib" "$WORK/upstream/test"
cat > "$WORK/upstream/package.json" <<'EOF'
{"name":"mindmaze","version":"1.0.0","type":"module",
 "scripts":{"test":"node --test test/*.test.js"}}
EOF
cat > "$WORK/upstream/src/lib/dashboardStats.js" <<'EOF'
export function projectBreakdown(projects) {
  const onTrack = projects.filter((p) => p.progress >= 50 && p.progress < 90).length;
  const atRisk = projects.filter((p) => p.progress >= 25 && p.progress < 50).length;
  const delayed = projects.filter((p) => p.progress < 25).length;
  return { total: projects.length, onTrack, atRisk, delayed };
}
EOF
cat > "$WORK/upstream/test/dashboardStats.test.js" <<'EOF'
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { projectBreakdown } from '../src/lib/dashboardStats.js';

test('every project lands in exactly one bucket', () => {
  const b = projectBreakdown([{progress:10},{progress:60},{progress:95},{progress:100}]);
  assert.equal(b.onTrack + b.atRisk + b.delayed + (b.completed ?? 0), b.total);
});
test('projects at 90 percent or more are reported as completed', () => {
  assert.equal(projectBreakdown([{progress:95},{progress:100}]).completed, 2);
});
EOF
( cd "$WORK/upstream" && git init -q && git add -A \
  && git -c user.email=t@t -c user.name=t commit -qm "initial" ) && ok || bad

step "stand up a stub for api.github.com"
python3 - "$WORK" > "$WORK/stub.log" 2>&1 &
STUB_PID=$!
sleep 0
cat > "$WORK/stub.py" <<'EOF'
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
WORK = sys.argv[1]
ISSUE = {"number": 12, "title":
         "Dashboard counts don't add up: projects at 90%+ are in no bucket",
         "body": "A user with projects at 10%, 60%, 95% and 100% sees "
                 "total: 4 but onTrack + atRisk + delayed = 2. Projects at "
                 "90% or more should be reported as `completed`.",
         "state": "open", "labels": [], "user": {"login": "reporter"},
         "html_url": "https://github.com/acme/mindmaze/issues/12"}
REPO = {"full_name": "acme/mindmaze", "default_branch": "master",
        "clone_url": f"file://{WORK}/upstream", "private": False}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.endswith("/issues/12"): body = ISSUE
        elif self.path.endswith("/comments"): body = []
        elif "/repos/" in self.path: body = REPO
        else: body = {}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers(); self.wfile.write(raw)
HTTPServer(("127.0.0.1", 8777), H).serve_forever()
EOF
kill "$STUB_PID" 2>/dev/null
python3 "$WORK/stub.py" "$WORK" >"$WORK/stub.log" 2>&1 &
STUB_PID=$!
for _ in $(seq 1 40); do
  curl -fsS http://127.0.0.1:8777/repos/acme/mindmaze >/dev/null 2>&1 && break
  sleep 0.25
done
curl -fsS http://127.0.0.1:8777/repos/acme/mindmaze >/dev/null 2>&1 && ok || bad "stub not up"

step "make setup"
( cd "$ROOT" && make setup ) >"$WORK/setup.log" 2>&1 && ok || bad "see $WORK/setup.log"

step 'make run ISSUE="https://github.com/acme/mindmaze/issues/12"'
cd "$ROOT"
OUT="$WORK/run.log"
AI_API_KEY="sk-ant-api03-FAKEEVALKEY1234567890" \
GITHUB_API_URL="http://127.0.0.1:8777" \
HARNESS_WORKSPACE="$WORK/ws" \
HARNESS_SMOKE_MOCK=1 \
PYTHONPATH="$ROOT/tests" \
PATH_TO_MOCK=1 \
.venv/bin/python -c "
import os, sys
sys.path.insert(0, 'tests'); sys.path.insert(0, '.')
import mock_model
class P:
    def setattr(self, o, n, v): setattr(o, n, v)
mock_model.install(P())
from harness.__main__ import main
sys.exit(main(['harness', 'https://github.com/acme/mindmaze/issues/12']))
" >"$OUT" 2>&1
RC=$?
printf "   exit %s\n" "$RC"
grep -qi "clone\|cloning" "$OUT" && printf "   cloned:   yes\n" || printf "   cloned:   NO\n"
CLONE=$(find "$WORK/ws" -maxdepth 2 -name package.json 2>/dev/null | head -1)
if [ -n "$CLONE" ]; then
  REPO_DIR="$(dirname "$CLONE")"
  printf "   into:     %s\n" "$REPO_DIR"
  printf "   branch:   %s\n" "$(git -C "$REPO_DIR" branch --show-current 2>/dev/null)"
  printf "   deps:     %s\n" "$([ -d "$REPO_DIR/node_modules" ] && echo installed || echo none)"
  CHANGED="$(git -C "$REPO_DIR" diff --name-only HEAD 2>/dev/null | tr '\n' ' ')"
  printf "   changed:  %s\n" "${CHANGED:-<nothing>}"
  if [ -n "$CHANGED" ]; then
    ( cd "$REPO_DIR" && npm test --silent >"$WORK/verify.log" 2>&1 )
    [ $? -eq 0 ] && printf "   tests:    PASS\n" || printf "   tests:    FAIL\n"
  fi
  [ -n "$CHANGED" ] && ok || bad "no patch written"
else
  bad "nothing was cloned"
fi

step "the report says what happened"
R="$(find "$WORK/ws" -name run_report.md 2>/dev/null | head -1)"
[ -n "$R" ] && head -5 "$R" | sed 's/^/   /' && ok || bad "no report"

printf "\n==============================\n"
[ "$FAIL" -eq 0 ] && echo "EVALUATOR GATE: PASS ($PASS checks)" \
                  || echo "EVALUATOR GATE: FAIL ($FAIL of $((PASS+FAIL)))"
echo "logs: $WORK"
exit 0
