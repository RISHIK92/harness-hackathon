"""Report the result back to GitHub (SPEC.md 30.1, FR-36).

Posting is OUTWARD-FACING: it writes to someone else's issue tracker, so it
is off unless explicitly enabled, it never fires on a failed run, and it
never pushes code.  `git push` remains on the runner's deny list; opening a
pull request is left to the operator, who can see the diff first.

    HARNESS_POST=comment   post the run report as a comment
    HARNESS_POST=off       (default) write the report to disk only
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from . import exits
from .github import API, GitHubError, Ref, _headers
from .model.http import request
from .model.types import ProviderError

POSTABLE_EXITS = (exits.SUCCESS, exits.PARTIAL)
MAX_BODY = 60_000


def enabled() -> bool:
    return os.environ.get("HARNESS_POST", "off").strip().lower() in (
        "comment", "1", "true", "yes", "on")


def _preamble(exit_code: int, summary) -> str:
    verdict = {exits.SUCCESS: "resolved", exits.PARTIAL: "partially resolved"}
    return (f"**Automated harness run: {verdict.get(exit_code, 'no fix')}.** "
            f"{summary.cycles} cycle(s). The diff is on branch "
            f"`{_branch()}`; nothing has been pushed.\n\n---\n\n")


def _branch() -> str:
    try:
        r = subprocess.run(["git", "branch", "--show-current"],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "(detached)"
    except (OSError, subprocess.SubprocessError):
        return "(unknown)"


def post_comment(ref: Ref, body: str, log) -> bool:
    """Post via gh when present, else the REST API. Returns success."""
    body = body[:MAX_BODY]
    if shutil.which("gh"):
        kind = "pr" if ref.is_pr else "issue"
        try:
            proc = subprocess.run(
                ["gh", kind, "comment", str(ref.number), "--repo", ref.slug,
                 "--body-file", "-"],
                # `input=` is this call's stdin; setting both raises.
                input=body, capture_output=True, text=True, timeout=60)
            if proc.returncode == 0:
                log.line(f"posted a comment on {ref}", phase="P5")
                return True
            log.warn(f"gh could not post ({proc.stderr.strip()[:120]}); "
                     "falling back to the API")
        except (OSError, subprocess.SubprocessError) as exc:
            log.warn(f"gh failed: {exc}")

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        log.warn("cannot post: no gh auth and no GITHUB_TOKEN. "
                 "The report is in .harness/run/run_report.md")
        return False
    try:
        request("POST",
                f"{API}/repos/{ref.slug}/issues/{ref.number}/comments",
                _headers(), {"body": body}, timeout=30)
    except ProviderError as exc:
        log.warn(f"could not post the comment: {exc}")
        return False
    log.line(f"posted a comment on {ref}", phase="P5")
    return True


def publish(cfg, log, ref, exit_code: int, summary, report: str) -> None:
    """Called once, at the very end of a run."""
    if not ref:
        return
    if not enabled():
        log.line(f"HARNESS_POST is off; the report for {ref} is in "
                 ".harness/run/run_report.md", phase="P5")
        return
    if exit_code not in POSTABLE_EXITS:
        log.line(f"not posting: the run did not produce a verified fix "
                 f"(exit {exit_code})", phase="P5")
        return
    post_comment(ref, _preamble(exit_code, summary) + report, log)
