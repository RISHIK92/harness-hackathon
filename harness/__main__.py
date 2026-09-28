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
    """Returns False when the clone was declined.

    Never in a service run: the service names the repository explicitly and
    has already cloned it. The first word of a ticket is the ticket author's
    text, and reading `other/repo#1` there as an instruction let whoever
    wrote the ticket point the run -- and its fix -- at another repository.
    """
    from . import consent, github
    from .config import service_run

    if service_run():
        return True

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

    The rule itself is `exits.publish_refusal`, shared with the service's
    /publish so the two can never disagree about what is good enough.
    """
    return exits.publish_refusal(exit_code, getattr(cfg, "_confidence", None),
                                 getattr(cfg, "_verify", None))


def _dirty_refusal(cfg) -> str:
    """"" when a run may start in `cfg.repo_path`, else why not.

    A run resets the working tree between attempts (`checkout -- .` and
    `clean`), which is right for a tree only the harness has touched and
    destroys an operator's uncommitted work in any other. So it does not
    start on one, unless told to with HARNESS_ALLOW_DIRTY=1.

    Not asked of a tree the harness made itself -- the clone of a GitHub
    reference, a service checkout -- nor of a dry run, which never resets.
    """
    from .config import env_bool, service_run
    if cfg.dry_run or cfg.github is not None or service_run() \
            or env_bool("HARNESS_ALLOW_DIRTY"):
        return ""
    from .repo.workspace import Workspace
    changed = Workspace(cfg.repo_path).uncommitted()
    if not changed:
        return ""
    shown = ", ".join(changed[:5])
    if len(changed) > 5:
        shown += f" and {len(changed) - 5} more"
    return (f"{cfg.repo_path} has uncommitted changes ({shown}). A run "
            f"resets the working tree between attempts, which would destroy "
            f"them. Commit or stash them first, or set HARNESS_ALLOW_DIRTY=1 "
            f"to run anyway.")


def _maybe_pr(cfg, log, gate, exit_code: int) -> None:
    """Unattended runs open a pull request only when explicitly allowed.

    Never in a service run. Publishing there is the service's /publish, with
    the caller's token; this path still COMMITTED locally before its push
    gate refused, which left /publish nothing to commit, so every successful
    run failed to publish.
    """
    from . import pullrequest as PR
    from .config import service_run
    if service_run():
        return
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
            refusal = _dirty_refusal(cfg)
            if refusal:
                log.warn(refusal)
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
        refusal = _dirty_refusal(cfg)
        if refusal:
            log.raw(f"config error: {refusal}")
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
