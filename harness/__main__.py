"""Entry point: `make run` -> python -m harness  (FR-1, FR-2, FR-4)."""
from __future__ import annotations

import os
import sys
import time
import traceback

from . import exits
from .config import ConfigError, load
from .logging_ui import Logger


def _secrets() -> list:
    return [os.environ.get(n, "") for n in
            ("AI_API_KEY", "GITHUB_TOKEN", "GH_TOKEN")]


def _resolve_github(cfg, log, confirm=None) -> bool:
    """Returns False when the operator backed out of a clone."""
    from . import github
    resolved = github.prepare(cfg.github_ref or cfg.issue, cfg, log, confirm)
    if resolved:
        cfg.issue, cfg.repo_path = resolved
        cfg.github = github.parse_ref(cfg.github_ref or cfg.issue or "")
        cfg.__post_init__()
        return True
    return not github.looks_like_ref(cfg.github_ref or cfg.issue or "")


def _run_once(cfg, log) -> int:
    from .orchestrator import Orchestrator
    return Orchestrator(cfg, log).run()


def _interactive(cfg, log) -> int:
    """The console: what the CLI does when it has a terminal and no issue.

    The run itself is identical to the scripted path -- same phases, same
    budgets, same verification, same report. Only the way the operator says
    what to work on differs.
    """
    from . import console as C
    from . import github

    ui = C.Console(log=log, cfg=cfg)
    last = exits.NO_FIX
    try:
        while True:
            choice = ui.select_task()
            if not choice:
                log.raw("")
                return last if last != exits.NO_FIX else exits.SUCCESS

            cfg.issue = choice.issue
            cfg.github_ref = choice.github_ref or choice.issue
            cfg.github = None
            if choice.repo is not None:
                cfg.repo_path = choice.repo
            cfg.__post_init__()
            cfg.conservative = False

            confirm = (lambda f, t, c: ui.confirm_github(f, t, c)) \
                if choice.github_ref else None
            try:
                if not _resolve_github(cfg, log, confirm):
                    continue
            except github.GitHubError as exc:
                log.warn(f"github: {exc}")
                continue

            started = time.time()
            last = _run_once(cfg, log)
            ui.remember(cfg.issue, cfg.repo_path, last, time.time() - started)

            report = cfg.work_dir / "run" / "run_report.md"
            if ui.after_run(last, cfg, report, cfg.github) != "again":
                return last
    finally:
        if getattr(ui.source, "close", None):
            ui.source.close()


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv
    log = Logger()

    try:
        cfg = load(argv)
    except ConfigError as exc:
        log.raw(f"config error: {exc}")
        return exits.CONFIG_ERROR

    log = Logger(level=cfg.log_level, secrets=_secrets())

    try:
        from . import console as C
        from . import github

        if C.interactive(cfg):
            return _interactive(cfg, log)

        if not cfg.issue:
            log.raw("No issue supplied. Use: make run ISSUE='<text>', "
                    "pipe it on stdin, or run make run at a terminal.")
            return exits.CONFIG_ERROR

        _resolve_github(cfg, log)
        return _run_once(cfg, log)
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
    finally:
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
