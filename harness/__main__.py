"""Entry point: `make run` -> python -m harness  (FR-1, FR-2, FR-4)."""
from __future__ import annotations

import os
import sys
import traceback

from . import exits
from .config import ConfigError, load
from .logging_ui import Logger


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv
    log = Logger()

    try:
        cfg = load(argv)
    except ConfigError as exc:
        log.raw(f"config error: {exc}")
        return exits.CONFIG_ERROR

    log = Logger(level=cfg.log_level,
                 secrets=[cfg.api_key, os.environ.get("GITHUB_TOKEN", ""),
                          os.environ.get("GH_TOKEN", "")])

    try:
        from . import github
        resolved = github.prepare(cfg.github_ref or cfg.issue, cfg, log)
        if resolved:
            cfg.issue, cfg.repo_path = resolved
            cfg.github = github.parse_ref(cfg.github_ref or "")
            cfg.__post_init__()          # work_dir follows the new repo
        from .orchestrator import Orchestrator
        return Orchestrator(cfg, log).run()
    except github.GitHubError as exc:
        log.raw(f"github: {exc}")
        return exits.CONFIG_ERROR
    except ConfigError as exc:
        log.raw(f"config error: {exc}")
        return exits.CONFIG_ERROR
    except KeyboardInterrupt:
        log.raw("interrupted")
        return exits.INTERNAL
    except Exception:
        err = cfg.work_dir / "run" / "error.log"
        try:
            err.parent.mkdir(parents=True, exist_ok=True)
            err.write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        log.raw("internal error; traceback written to .harness/run/error.log")
        if cfg.log_level == "debug":
            traceback.print_exc()
        return exits.INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
