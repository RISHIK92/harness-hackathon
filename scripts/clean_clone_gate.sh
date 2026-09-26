#!/usr/bin/env bash
# T10.1 -- the acceptance run (SPEC.md 32, IMPLEMENTATION.md 16).
# Clone into a scratch directory, build from nothing, run the exact sequence
# an evaluator runs. Must pass on a machine that has never built this project.
set -u
SRC="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
FAIL=0
step() { printf '\n== %s\n' "$1"; }
check() { if [ "$1" -eq 0 ]; then echo "   ok"; else echo "   FAIL"; FAIL=1; fi; }

step "clone"
git clone -q "$SRC" "$WORK/repo"; check $?
cd "$WORK/repo" || exit 1

step "no credential in the clone"
! git grep -qE 'sk-ant-api[0-9]{2}-[A-Za-z0-9_-]{16,}' -- ':!tests' ':!bench' ':!scripts'
check $?

step "make setup (cold, no network beyond pip)"
make setup >"$WORK/setup.log" 2>&1; check $?
tail -3 "$WORK/setup.log" | sed 's/^/   /'

step "make run without a key exits non-zero with a usage line"
out=$(make run 2>&1); rc=$?
[ $rc -ne 0 ] && echo "$out" | grep -q "AI_API_KEY is not set"; check $?

step "build a target repository"
.venv/bin/python scripts/make_fixtures.py >/dev/null 2>&1; check $?

step "make run against it"
AI_API_KEY="sk-ant-api03-FAKEGATEKEY1234567890" \
REPO_PATH="$WORK/repo/tests/fixtures/py-offbyone" \
PYTHONPATH="$WORK/repo/tests" \
HARNESS_SMOKE_MOCK=1 \
.venv/bin/python -c "
import os, sys
sys.path.insert(0, 'tests'); sys.path.insert(0, '.')
import mock_model
class P:
    def setattr(self, o, n, v): setattr(o, n, v)
mock_model.install(P())
os.environ['ISSUE'] = 'parse_date crashes with IndexError when the input has no separator. Expected a ValueError; got IndexError: list index out of range.'
from harness.__main__ import main
sys.exit(main(['harness']))
" >"$WORK/run.log" 2>&1
rc=$?; [ $rc -eq 0 ]; check $?
grep -E "^(status|confidence|files changed)" "$WORK/run.log" | sed 's/^/   /'

step "the fix actually works"
(cd tests/fixtures/py-offbyone && "$WORK/repo/.venv/bin/python" -m pytest -q >/dev/null 2>&1); check $?

step "make test replays with NO api key"
env -u AI_API_KEY make test >"$WORK/replay.log" 2>&1; check $?
grep -E "steps|reproduced" "$WORK/replay.log" | sed 's/^/   /'

printf '\n==============================\n'
if [ $FAIL -eq 0 ]; then echo "CLEAN-CLONE GATE: PASS"; else echo "CLEAN-CLONE GATE: FAIL"; fi
exit $FAIL
