#!/usr/bin/env bash
# Run the harness against a fixture repository without a real API key.
#
#   scripts/demo.sh                  # py-offbyone, full run
#   scripts/demo.sh py-red-baseline  # a repo with pre-existing failures
#   scripts/demo.sh py-vague --dry   # investigation only, writes nothing
#
# Uses the scripted stand-in model in tests/mock_model.py, so it is offline
# and deterministic. Everything except the model is the real harness.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURE="${1:-py-offbyone}"
DRY=""
[ "${2:-}" = "--dry" ] && DRY=1

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "run 'make setup' first"; exit 1; }
[ -d "$ROOT/tests/fixtures/$FIXTURE" ] || "$PY" "$ROOT/scripts/make_fixtures.py" >/dev/null
[ -d "$ROOT/tests/fixtures/$FIXTURE" ] || { echo "no such fixture: $FIXTURE"; exit 1; }

REPO="$ROOT/tests/fixtures/$FIXTURE"
ISSUE="$("$PY" -c "
import json,sys
print(json.load(open('$REPO/.fixture.json'))['issue'])")"

git -C "$REPO" reset -q && git -C "$REPO" checkout -- . 2>/dev/null || true

echo "fixture: $FIXTURE"
echo "issue:   ${ISSUE%%$'\n'*}"
echo

cd "$ROOT"
# Exported rather than placed inline: a word produced by expansion is not
# parsed as an assignment, so `${DRY:+VAR=1} cmd` runs VAR=1 as the command.
export AI_API_KEY="sk-ant-api03-FAKEDEMOKEY1234567890"
export REPO_PATH="$REPO"
export ISSUE
[ -n "$DRY" ] && export HARNESS_DRY_RUN=1
"$PY" -c "
import sys, os
sys.path.insert(0, 'tests'); sys.path.insert(0, '.')
import mock_model
class P:
    def setattr(self, o, n, v): setattr(o, n, v)
mock_model.install(P())
from harness.__main__ import main
sys.exit(main(['harness']))
"
rc=$?

echo
echo "artifacts: $REPO/.harness/run/"
echo "to reset:  git -C $REPO checkout -- ."
exit $rc
