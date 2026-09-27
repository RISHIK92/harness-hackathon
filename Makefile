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

# LIVE_ENV=True lets the harness prepare this machine -- install dependencies,
# create a virtualenv, start a container -- so the suite can run. Off by
# default: cloning and editing needs none of it, and preparing an
# environment changes the working copy before the first edit is proposed.
#
# PR=True opens a pull request when the fix verifies; PR=False forbids it.
# Leaving PR unset keeps the default: the harness asks at a terminal, and
# declines when there is nobody to ask. Cloning and installing are allowed
# either way -- they are how the repository gets read and run at all.
run:
	@if [ -z "$$AI_API_KEY" ]; then echo "Error: AI_API_KEY is not set."; echo "Usage: AI_API_KEY=... make run ISSUE='<issue text or GitHub URL>' [PR=True]"; exit 1; fi
	@case "$(LIVE_ENV)" in \
	  True|true|TRUE|yes|YES|1|on) \
	    echo "  LIVE_ENV=True: dependencies may be installed to run the suite"; \
	    export HARNESS_LIVE_ENV=1 ;; \
	  ""|False|false|FALSE|no|NO|0|off) : ;; \
	  *) echo "Error: LIVE_ENV must be True or False (got '$(LIVE_ENV)')."; exit 1 ;; \
	esac; \
	case "$(PR)" in \
	  True|true|TRUE|yes|YES|1|on) \
	    echo "  PR=True: a verified fix will be pushed and a pull request opened"; \
	    HARNESS_AUTO="clone,install,push,pr" $(BIN)/python -m harness ;; \
	  False|false|FALSE|no|NO|0|off) \
	    HARNESS_AUTO="clone,install" $(BIN)/python -m harness ;; \
	  "") \
	    $(BIN)/python -m harness ;; \
	  *) \
	    echo "Error: PR must be True or False (got '$(PR)')."; exit 1 ;; \
	esac

test:
	@$(BIN)/python -m harness.replay || $(BIN)/python -m pytest tests/unit -q

clean:
	rm -rf $(VENV) .harness .pytest_cache .worktrees
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
