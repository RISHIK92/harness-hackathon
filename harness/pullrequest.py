"""Commit, push and open a pull request (SPEC.md 30.1).

Every step here leaves the machine, so every step goes through the consent
gate. `git push` stays on the runner's deny list for model-issued commands;
the harness issues this one itself, after being allowed to.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from . import consent
from .github import API, _headers, clone_auth_env
from .model.http import request
from .model.types import ProviderError
from .verify.runner import run

TITLE_LIMIT = 70          # the whole subject line, not just the text


@dataclass
class Result:
    ok: bool = False
    branch: str = ""
    url: str = ""
    compare_url: str = ""
    reason: str = ""

    def render(self) -> str:
        if self.url:
            return f"pull request opened: {self.url}"
        if self.compare_url:
            return f"branch pushed: {self.compare_url}"
        return self.reason or "nothing was pushed"


def title_for(root_cause, issue, ref=None) -> str:
    """A conventional-commit subject line from the root cause."""
    kind = {"config": "fix", "external": "fix", "logic": "fix",
            "null": "fix", "type": "fix", "race": "fix",
            "missing_case": "fix"}.get(
        getattr(root_cause, "classification", ""), "fix")
    text = (getattr(root_cause, "statement", "") or
            getattr(issue, "title", "") or "resolve the reported issue")
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    text = text[0].lower() + text[1:] if text else text
    suffix = f" (#{ref.number})" if ref else ""
    room = TITLE_LIMIT - len(kind) - 2 - len(suffix)
    if len(text) > room:
        text = text[:max(1, room - 1)].rsplit(" ", 1)[0] + "\u2026"
    return f"{kind}: {text}{suffix}"


def body_for(report: str, ref=None, summary=None) -> str:
    """The run report, with a short preamble stating what produced it."""
    head = ["Opened by an automated coding harness."]
    if ref:
        head.append(f"Closes #{ref.number}.")
    if summary is not None:
        head.append(f"{getattr(summary, 'cycles', '?')} fix-and-verify "
                    f"cycle(s); every claim below is backed by a command the "
                    f"harness ran.")
    head.append("")
    head.append("---")
    head.append("")
    return "\n".join(head) + report


def _git(repo, args: str, timeout: float = 120.0, env: dict | None = None):
    # Harness-issued, after the consent gate: `git push` is on the deny list
    # precisely so that only this path, never a model, can push.
    return run(f"git {args}", repo, timeout=timeout, check_deny=False,
               env_extra=env)


def _push_env(repo) -> dict:
    """What the harness's own push needs from the operator's environment.

    The runner scrubs credentials from every command, because most of them
    run the repository's code. This one is the operator's push: their SSH
    agent, and GITHUB_TOKEN for an https remote -- sent as a header, since a
    clone no longer writes the token into the remote URL.
    """
    env = {}
    if os.environ.get("SSH_AUTH_SOCK"):
        env["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
    url = _git(repo, "remote get-url origin", timeout=15).stdout.strip()
    env.update(clone_auth_env(url))
    return env


def current_branch(repo) -> str:
    r = _git(repo, "branch --show-current", timeout=15)
    return r.stdout.strip()


def default_branch(repo) -> str:
    r = _git(repo, "symbolic-ref --short refs/remotes/origin/HEAD", timeout=15)
    name = r.stdout.strip().split("/")[-1]
    return name or "main"


def remote_slug(repo) -> str:
    r = _git(repo, "remote get-url origin", timeout=15)
    url = r.stdout.strip()
    m = re.search(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def open_pr(repo, gate: consent.Gate, log, root_cause, issue, report: str,
            summary=None, ref=None) -> Result:
    """Commit, push, open. Each outward step asks first."""
    slug = remote_slug(repo)
    if not slug:
        return Result(reason="origin is not a GitHub remote")

    branch = current_branch(repo) or f"harness/fix-{os.getpid()}"
    base = default_branch(repo)
    title = title_for(root_cause, issue, ref)

    dirty = bool(_git(repo, "status --porcelain", timeout=15).stdout.strip())
    # Already committed is not the same as nothing to offer. A cycle that
    # checkpointed, or an operator who committed by hand, leaves a clean tree
    # with real work sitting on the branch -- refusing there loses the work.
    ahead = _git(repo, f"rev-list --count {base}..HEAD",
                 timeout=15).stdout.strip()
    if not dirty and ahead in ("", "0"):
        return Result(reason="nothing to commit and nothing ahead of "
                             f"{base}")

    if dirty:
        # Committing is local, so it needs no permission.
        _git(repo, "add -A")
        commit = _git(repo, f'-c user.email=harness@local '
                            f'-c user.name=harness '
                            f'commit -q -m {json.dumps(title)}')
        if not commit.ok:
            return Result(reason=f"commit failed: {commit.output[-200:]}")
        log.ok("committed", title, plain=f"committed: {title}")
    else:
        log.line(f"{ahead} commit(s) already on {branch}")

    rows = [("repository", slug), ("branch", f"{branch} -> {base}"),
            ("title", title),
            ("size", _git(repo, "diff --shortstat HEAD~1",
                          timeout=15).stdout.strip() or "unknown")]
    if not gate.allow(consent.PUSH, f"push {branch} to {slug}", rows):
        return Result(branch=branch, reason="push was not allowed")

    push = _git(repo, f"push --set-upstream origin {branch}",
                env=_push_env(repo))
    if not push.ok:
        return Result(branch=branch,
                      reason=f"push failed: {push.output[-200:]}")
    compare = f"https://github.com/{slug}/compare/{base}...{branch}?expand=1"
    log.ok("pushed", branch, plain=f"pushed: {branch}")

    if not gate.allow(consent.PR, f"open a pull request on {slug}",
                      [("title", title), ("base", base), ("head", branch)]):
        return Result(ok=True, branch=branch, compare_url=compare,
                      reason="pull request was not opened")

    url = _create(slug, title, body_for(report, ref, summary), base, branch,
                  log)
    if url:
        return Result(ok=True, branch=branch, url=url, compare_url=compare)
    return Result(ok=True, branch=branch, compare_url=compare,
                  reason="could not open the pull request; the branch is "
                         "pushed and the compare link is above")


def _create(slug: str, title: str, body: str, base: str, head: str,
            log) -> str:
    if shutil.which("gh"):
        try:
            proc = subprocess.run(
                ["gh", "pr", "create", "--repo", slug, "--base", base,
                 "--head", head, "--title", title, "--body-file", "-"],
                # `input=` IS the stdin for this call, so it must not also be
            # DEVNULL: passing both is a ValueError, and it broke opening a
            # pull request through `gh` entirely.
            input=body, capture_output=True, text=True, timeout=90)
            if proc.returncode == 0:
                found = re.search(r"https://\S+", proc.stdout)
                if found:
                    return found.group(0)
            log.debug(f"gh pr create: {proc.stderr.strip()[:160]}")
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug(f"gh pr create failed: {exc}")

    if not (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")):
        log.warn("cannot open the pull request: no gh auth and no "
                 "GITHUB_TOKEN")
        return ""
    try:
        out = request("POST", f"{API}/repos/{slug}/pulls", _headers(),
                      {"title": title, "body": body, "base": base,
                       "head": head}, timeout=60)
        return str(out.get("html_url") or "")
    except ProviderError as exc:
        log.warn(f"could not open the pull request: {exc}")
        return ""
