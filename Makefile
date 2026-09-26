.PHONY: setup run test clean
PY ?= python3
VENV := .venv
BIN := $(VENV)/bin

setup:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --quiet --upgrade pip
	$(BIN)/pip install --quiet -r requirements-core.txt
	-$(BIN)/pip install --quiet -r requirements-optional.txt
	@$(BIN)/python -m harness.setup_ui

run:
	@if [ -z "$$AI_API_KEY" ]; then echo "Error: AI_API_KEY is not set."; echo "Usage: AI_API_KEY=... make run ISSUE='<issue text>'"; exit 1; fi
	@$(BIN)/python -m harness

test:
	@$(BIN)/python -m harness.replay || $(BIN)/python -m pytest tests/unit -q

clean:
	rm -rf $(VENV) .harness .pytest_cache .worktrees
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
