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


def _resolve_github(cfg, log, gate=None) -> bool:
    """Returns False when the clone was declined."""
    from . import consent, github

    confirm = None
    if gate is not None:
        def confirm(fetched, target, _cfg):
            return gate.allow(
                consent.CLONE, f"clone {fetched.ref.slug}",
                [("issue", f"#{fetched.ref.number} "
                           f"{fetched.title[:44]}"),
                 ("into", str(target)),
                 ("branch", f"pr-{fetched.ref.number}" if fetched.ref.is_pr
                            else f"harness/issue-{fetched.ref.number}")],
                requested=True)

    resolved = github.prepare(cfg.github_ref or cfg.issue, cfg, log, confirm)
    if resolved:
        cfg.issue, cfg.repo_path = resolved
        cfg.github = github.parse_ref(cfg.github_ref or cfg.issue or "")
        cfg.__post_init__()
        return True
    return not github.looks_like_ref(cfg.github_ref or cfg.issue or "")


def _publishable(cfg, exit_code: int) -> str:
    """"" when the run may be proposed to a human, else why not.

    The exit code alone is not evidence. A PARTIAL run can have a failed
    HARD gate -- a broken test, a file changed outside the plan -- and one
    was opened as a pull request that turned `return 0` into
    `return "No data"` and broke the suite. Proposing a change that fails
    its own verification is the single thing this harness exists to prevent.
    """
    if exit_code == exits.SUCCESS:
        return ""
    if exit_code != exits.PARTIAL:
        return f"the run did not produce a fix (exit {exit_code})"

    conf = getattr(cfg, "_confidence", None)
    if conf is None:
        return "no confidence report to judge the change by"
    failed = [c for c in conf.HARD if not getattr(conf, c, False)]
    if failed:
        return "a hard gate failed: " + ", ".join(failed)

    verify = getattr(cfg, "_verify", None)
    for cls in (getattr(verify, "full", None), getattr(verify, "scoped", None)):
        if cls is not None and getattr(cls, "new", None):
            return (f"the change introduces {len(cls.new)} new test "
                    f"failure(s): {', '.join(cls.new[:3])}")
    if getattr(verify, "oracle_passes", None) is False:
        return "the reproduction test still fails"
    return ""


def _maybe_pr(cfg, log, gate, exit_code: int) -> None:
    """Unattended runs open a pull request only when explicitly allowed."""
    from . import pullrequest as PR
    refusal = _publishable(cfg, exit_code)
    if refusal:
        log.note("no_pr", f"not opening a pull request: {refusal}")
        return
    if not getattr(cfg, "_root_cause", None):
        return
    report = cfg.work_dir / "run" / "run_report.md"
    text = report.read_text("utf-8", errors="replace") if report.is_file() \
        else ""
    result = PR.open_pr(cfg.repo_path, gate, log, cfg._root_cause,
                        cfg._issue, text, cfg._summary, cfg.github)
    if result.url or result.compare_url:
        log.raw(result.render())


def _run_once(cfg, log, gate=None, clarify=None) -> int:
    from .orchestrator import Orchestrator
    return Orchestrator(cfg, log, gate=gate, clarify=clarify).run()


def _interactive(cfg, log) -> int:
    """The console: what the CLI does when it has a terminal and no issue.

    The run itself is identical to the scripted path -- same phases, same
    budgets, same verification, same report. Only the way the operator says
    what to work on differs.
    """
    from . import console as C
    from . import consent, github
    from . import pullrequest as PR

    ui = C.Console(log=log, cfg=cfg)
    policy = consent.Policy.from_env()
    gate = consent.Gate(policy, log, confirm=ui.consent_card)
    owned = ui.take_terminal()
    last = exits.NO_FIX
    try:
        while True:
            choice = ui.select_task()
            if not choice:
                return last if last != exits.NO_FIX else exits.SUCCESS

            cfg.issue = choice.issue
            cfg.github_ref = choice.github_ref or choice.issue
            cfg.github = None
            if choice.repo is not None:
                cfg.repo_path = choice.repo
            cfg.__post_init__()
            cfg.conservative = False

            try:
                if not _resolve_github(cfg, log, gate):
                    continue
            except github.GitHubError as exc:
                log.warn(f"github: {exc}")
                continue

            started = time.time()
            last = _run_once(cfg, log, gate, clarify=ui.clarify)
            ui.remember(cfg.issue, cfg.repo_path, last, time.time() - started)

            report = cfg.work_dir / "run" / "run_report.md"

            def raise_pr(_cfg=cfg, _last=last, _report=report):
                # The same bar as an unattended run. Choosing "open a pull
                # request" from a menu is not evidence that the change is
                # good; the gates are.
                refusal = _publishable(_cfg, _last)
                if refusal:
                    return PR.Result(reason=f"not proposed: {refusal}")
                text = _report.read_text("utf-8", errors="replace") \
                    if _report.is_file() else ""
                return PR.open_pr(_cfg.repo_path, gate, log,
                                  _cfg._root_cause, _cfg._issue, text,
                                  _cfg._summary, _cfg.github)

            hook = raise_pr if getattr(cfg, "_root_cause", None) else None
            if ui.after_run(last, cfg, report, cfg.github, hook) != "again":
                return last
    finally:
        ui.source.close()
        transcript = ui.release_terminal()
        if owned and transcript.strip():
            # Hand the session back to scrollback: a full-screen UI that
            # swallows its own output would be worse than no UI.
            print(transcript)


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

        from . import consent
        policy = consent.Policy.from_env()

        if C.interactive(cfg):
            return _interactive(cfg, log)

        gate = consent.Gate(policy, log, confirm=None)

        if not cfg.issue:
            log.raw("No issue supplied. Use: make run ISSUE='<text>', "
                    "pipe it on stdin, or run make run at a terminal.")
            return exits.CONFIG_ERROR

        if not _resolve_github(cfg, log, gate):
            log.raw("the clone was not permitted; nothing to do")
            return exits.CONFIG_ERROR
        code = _run_once(cfg, log, gate)
        _maybe_pr(cfg, log, gate, code)
        return code
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
