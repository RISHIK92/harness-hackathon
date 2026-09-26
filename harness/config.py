"""Configuration: the single source of truth for runtime settings.

Precedence everywhere is: explicit env > discovery > heuristic default.
An unset variable stays unset -- it never becomes "", because an empty string
defeats every os.getenv(name, default) downstream.  SPEC.md 12, 35.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    """Fatal configuration problem. Maps to exit code 4."""


def env(name: str, default=None):
    """Read an env var, treating empty/whitespace as unset."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw if raw else default


def env_int(name: str, default: int) -> int:
    raw = env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}")


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


# Phase share of the global token budget (SPEC.md 10.3).
PHASE_SHARE = {
    "P0": 0.02, "P1": 0.35, "P2": 0.08, "P3": 0.25, "P4": 0.18, "P5": 0.12,
}

TIER_FACTOR = {"T0": 0.35, "T1": 0.55, "T2": 0.75}
TIER_RECENT_K = {"T0": 6, "T1": 10, "T2": 16}
TIER_STEPS_P1 = {"T0": 10, "T1": 14, "T2": 18}
TIER_SAMPLES = {"T0": 4, "T1": 2, "T2": 1}
TIER_MAP_TOKENS = {"T0": 800, "T1": 2000, "T2": 4000}


@dataclass
class Config:
    """Resolved runtime configuration."""

    api_key: str
    issue: str
    repo_path: Path

    base_url: str | None = None
    provider: str | None = None
    model: str | None = None
    cheap_model: str | None = None
    tier: str | None = None

    max_cycles: int = 5
    token_budget: int = 900_000
    time_budget: int = 1500
    ctx_cap: int = 200_000

    test_cmd: str | None = None
    lint_cmd: str | None = None

    no_cache: bool = False
    no_cache_prompt: bool = False
    no_coverage: bool = False
    no_bisect: bool = False
    log_level: str = "info"
    dry_run: bool = False

    github_ref: str | None = None
    task_type: str | None = None
    route: str | None = None
    known_good: str | None = None
    parallel: int | None = None
    replay: str | None = None

    # Mutable run state (set by phases, not by env).
    conservative: bool = False
    github: object = None          # the resolved Ref, when one was supplied
    oracle: str | None = None
    effective_tier: str = "T1"

    work_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.work_dir = self.repo_path / ".harness"

    # -- derived -----------------------------------------------------------
    def phase_budget(self, phase: str) -> int:
        return int(self.token_budget * PHASE_SHARE.get(phase, 0.1))

    def context_budget(self, model_ctx: int) -> int:
        """Never a hardcoded constant: derived from the model's real window."""
        window = min(model_ctx or 32_000, self.ctx_cap)
        return int(window * TIER_FACTOR.get(self.effective_tier, 0.55))

    def samples(self) -> int:
        if self.conservative:
            return 1
        return TIER_SAMPLES.get(self.effective_tier, 1)

    def recent_k(self) -> int:
        return TIER_RECENT_K.get(self.effective_tier, 10)

    def p1_steps(self) -> int:
        return TIER_STEPS_P1.get(self.effective_tier, 14)

    def map_tokens(self) -> int:
        return TIER_MAP_TOKENS.get(self.effective_tier, 2000)


def read_issue(argv: list[str]) -> str:
    """FR-1: argv > ISSUE > ISSUE_FILE > stdin (non-TTY) > prompt (TTY only).

    Never blocks on input in a non-interactive run.
    """
    positional = [a for a in argv[1:] if not a.startswith("-")]
    if positional:
        return " ".join(positional)

    issue = env("ISSUE")
    if issue:
        return issue

    path = env("ISSUE_FILE")
    if path:
        p = Path(path)
        if not p.is_file():
            raise ConfigError(f"ISSUE_FILE does not exist: {path}")
        return p.read_text(encoding="utf-8", errors="replace")

    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            return data
        raise ConfigError(
            "No issue supplied. Use: make run ISSUE='<text>' "
            "or pipe the issue on stdin."
        )

    print("Paste the issue, then Ctrl-D:", file=sys.stderr)
    data = sys.stdin.read()
    if not data.strip():
        raise ConfigError("No issue supplied.")
    return data


def load(argv: list[str] | None = None) -> Config:
    """Build the Config from the environment. Raises ConfigError (exit 4)."""
    argv = argv if argv is not None else sys.argv

    api_key = env("AI_API_KEY")
    if not api_key:
        raise ConfigError(
            "AI_API_KEY is not set. Usage: AI_API_KEY=... make run ISSUE='...'"
        )

    repo_path = Path(env("REPO_PATH", os.getcwd())).resolve()
    if not repo_path.is_dir():
        raise ConfigError(f"REPO_PATH is not a directory: {repo_path}")

    replay = env("HARNESS_REPLAY")
    issue = "" if replay else read_issue(argv)

    # A GitHub reference is resolved to issue text and a clone before
    # anything else runs, so every phase downstream sees an ordinary local
    # repository and ordinary issue text.
    github_ref = env("GITHUB_ISSUE") or env("GITHUB_PR") or issue

    tier = env("HARNESS_TIER")
    if tier and tier.upper() not in TIER_FACTOR:
        raise ConfigError(f"HARNESS_TIER must be T0, T1 or T2 (got {tier!r})")

    cfg = Config(
        api_key=api_key,
        issue=issue,
        github_ref=github_ref,
        repo_path=repo_path,
        base_url=env("AI_BASE_URL"),
        provider=env("HARNESS_PROVIDER"),
        model=env("HARNESS_MODEL"),
        cheap_model=env("HARNESS_CHEAP_MODEL"),
        tier=tier.upper() if tier else None,
        max_cycles=env_int("HARNESS_MAX_CYCLES", 5),
        token_budget=env_int("HARNESS_TOKEN_BUDGET", 900_000),
        time_budget=env_int("HARNESS_TIME_BUDGET", 1500),
        ctx_cap=env_int("HARNESS_CTX_CAP", 200_000),
        test_cmd=env("HARNESS_TEST_CMD"),
        lint_cmd=env("HARNESS_LINT_CMD"),
        no_cache=env_bool("HARNESS_NO_CACHE"),
        no_cache_prompt=env_bool("HARNESS_NO_CACHE_PROMPT"),
        no_coverage=env_bool("HARNESS_NO_COVERAGE"),
        no_bisect=env_bool("HARNESS_NO_BISECT"),
        log_level=env("HARNESS_LOG_LEVEL", "info"),
        dry_run=env_bool("HARNESS_DRY_RUN"),
        task_type=env("HARNESS_TASK_TYPE"),
        route=env("HARNESS_ROUTE"),
        known_good=env("HARNESS_KNOWN_GOOD"),
        parallel=env_int("HARNESS_PARALLEL", 0) or None,
        replay=replay,
    )
    if cfg.tier:
        cfg.effective_tier = cfg.tier
    return cfg
