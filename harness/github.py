"""Fetch an issue or PR from GitHub, and clone the repository it belongs to.

No new dependency: the REST API is reached with the same stdlib `urllib` the
provider adapters use.  `gh` is used when it happens to be installed --  it
handles SSO and enterprise auth better than a raw token -- but it is never
required, because installing it would need sudo/apt and `make setup` must
succeed on a clean machine without either (NFR-3).

Accepted references:
    owner/repo#123
    https://github.com/owner/repo/issues/123
    https://github.com/owner/repo/pull/456
    github.com/owner/repo/pull/456
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .model.http import request
from .model.types import ProviderError
from .verify.runner import run

API = "https://api.github.com"

REF_PATTERNS = (
    re.compile(r"^(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)#(?P<number>\d+)$"),
    re.compile(r"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>[\w.-]+)/"
               r"(?P<repo>[\w.-]+)/(?P<kind>issues|pull)/(?P<number>\d+)"),
)


class GitHubError(Exception):
    """Fatal problem fetching or cloning. Maps to exit code 4."""


@dataclass
class Ref:
    owner: str
    repo: str
    number: int
    kind: str = "issues"          # issues | pull

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def is_pr(self) -> bool:
        return self.kind == "pull"

    def __str__(self) -> str:
        return f"{self.slug}#{self.number}"


@dataclass
class Fetched:
    ref: Ref
    title: str
    body: str
    comments: list = field(default_factory=list)
    clone_url: str = ""
    default_branch: str = "main"
    head_ref: str = ""            # PRs only
    labels: list = field(default_factory=list)
    state: str = ""

    def as_issue_text(self) -> str:
        """Everything a human would read, in the order they would read it.

        Comments carry the repro steps and stack traces far more often than
        the body does, and those are exactly the anchors P0 extracts.
        """
        parts = [self.title, ""]
        if self.labels:
            parts.append("labels: " + ", ".join(self.labels))
            parts.append("")
        parts.append(self.body or "(no description)")
        for i, c in enumerate(self.comments, 1):
            author = c.get("author") or "someone"
            body = (c.get("body") or "").strip()
            if not body:
                continue
            parts += ["", f"--- comment {i} by {author} ---", body]
        return "\n".join(parts).strip()


def parse_ref(text: str) -> Ref | None:
    """Recognise a GitHub reference. Returns None for ordinary issue text."""
    candidate = (text or "").strip().split()[0] if (text or "").strip() else ""
    for pattern in REF_PATTERNS:
        m = pattern.match(candidate)
        if not m:
            continue
        groups = m.groupdict()
        kind = groups.get("kind") or "issues"
        return Ref(owner=groups["owner"], repo=groups["repo"].removesuffix(".git"),
                   number=int(groups["number"]), kind=kind)
    return None


def looks_like_ref(text: str) -> bool:
    return parse_ref(text) is not None


# ---------------------------------------------------------------- fetching
def _token() -> str | None:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _headers() -> dict:
    h = {"Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    token = _token()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _api(path: str, timeout: float = 15.0):
    try:
        return request("GET", f"{API}{path}", _headers(), timeout=timeout)
    except ProviderError as exc:
        if exc.status == 404:
            raise GitHubError(
                f"{path} not found. If the repository is private, set "
                f"GITHUB_TOKEN.") from exc
        if exc.status == 403:
            raise GitHubError(
                "GitHub rate limit or access denied. Set GITHUB_TOKEN to "
                "raise the limit from 60 to 5000 requests an hour.") from exc
        raise GitHubError(f"GitHub API: {exc}") from exc


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _gh_json(args: list) -> dict | None:
    """Use the CLI when present: it already holds the user's auth."""
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True,
                              timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def fetch(ref: Ref, log=None) -> Fetched:
    """Issue or PR, with its comments, plus what is needed to clone."""
    via = "gh" if _gh_available() else "api"
    if log:
        log.line(f"fetching {ref} via {via}", phase="P0")

    data = comments = None
    if via == "gh":
        kind = "pr" if ref.is_pr else "issue"
        data = _gh_json([kind, "view", str(ref.number), "--repo", ref.slug,
                         "--json",
                         "title,body,comments,labels,state"
                         + (",headRefName" if ref.is_pr else "")])
        if data is not None:
            comments = [{"author": (c.get("author") or {}).get("login"),
                         "body": c.get("body")}
                        for c in data.get("comments", [])]
    if data is None:                       # no gh, or gh could not answer
        endpoint = "pulls" if ref.is_pr else "issues"
        data = _api(f"/repos/{ref.slug}/{endpoint}/{ref.number}")
        raw = _api(f"/repos/{ref.slug}/issues/{ref.number}/comments")
        comments = [{"author": (c.get("user") or {}).get("login"),
                     "body": c.get("body")} for c in (raw or [])]
        if ref.is_pr:
            review = _api(f"/repos/{ref.slug}/pulls/{ref.number}/comments")
            comments += [{"author": (c.get("user") or {}).get("login"),
                          "body": f"[on {c.get('path')}] {c.get('body')}"}
                         for c in (review or [])]

    repo_meta = _api(f"/repos/{ref.slug}")
    head = data.get("head") or {}

    return Fetched(
        ref=ref,
        title=str(data.get("title") or f"{ref}"),
        body=str(data.get("body") or ""),
        comments=comments or [],
        clone_url=repo_meta.get("clone_url") or
        f"https://github.com/{ref.slug}.git",
        default_branch=repo_meta.get("default_branch") or "main",
        head_ref=str(data.get("headRefName") or head.get("ref") or ""),
        labels=[l.get("name") if isinstance(l, dict) else str(l)
                for l in (data.get("labels") or [])],
        state=str(data.get("state") or ""),
    )


# ---------------------------------------------------------------- cloning
def clone(fetched: Fetched, workspace: Path, log=None,
          depth: int = 0) -> Path:
    """Clone into `workspace` and check out the right starting point.

    For a PR the branch is fetched through `pull/N/head`, which works whether
    the PR comes from a branch or from a fork.
    """
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / f"{fetched.ref.repo}-{fetched.ref.number}"

    if target.exists():
        if (target / ".git").is_dir():
            if log:
                log.line(f"reusing {target}", phase="P0")
            return target
        shutil.rmtree(target, ignore_errors=True)

    url = _authed_url(fetched.clone_url)
    flags = f"--depth {depth}" if depth else ""
    if log:
        log.line(f"cloning {fetched.ref.slug} -> {target}", phase="P0")
    result = run(f"git clone --quiet {flags} {url} {target}", workspace,
                 timeout=600, check_deny=False)
    if not result.ok:
        raise GitHubError(f"clone failed: {result.output[-300:]}")

    if fetched.ref.is_pr:
        branch = f"pr-{fetched.ref.number}"
        fetch_cmd = (f"git fetch --quiet origin "
                     f"pull/{fetched.ref.number}/head:{branch}")
        if run(fetch_cmd, target, timeout=300, check_deny=False).ok:
            run(f"git checkout --quiet {branch}", target, timeout=60,
                check_deny=False)
            if log:
                log.line(f"checked out {branch}", phase="P0")
        elif log:
            log.warn(f"could not fetch pull/{fetched.ref.number}/head; "
                     f"staying on {fetched.default_branch}")
    else:
        branch = f"harness/issue-{fetched.ref.number}"
        run(f"git checkout --quiet -b {branch}", target, timeout=60,
            check_deny=False)
        if log:
            log.line(f"working on {branch}", phase="P0")
    return target


def _authed_url(url: str) -> str:
    """Embed a token for private repositories, without logging it."""
    token = _token()
    if token and url.startswith("https://github.com/"):
        return url.replace("https://", f"https://x-access-token:{token}@", 1)
    return url


def prepare(issue_text: str, cfg, log) -> tuple[str, Path] | None:
    """If the issue is a GitHub reference, fetch it and clone. Else None."""
    ref = parse_ref(issue_text)
    if not ref:
        return None
    fetched = fetch(ref, log)
    workspace = Path(os.environ.get("HARNESS_WORKSPACE")
                     or (Path.cwd() / ".harness" / "workspace"))
    depth = int(os.environ.get("HARNESS_CLONE_DEPTH", "0") or 0)
    repo = clone(fetched, workspace, log, depth)
    text = fetched.as_issue_text()
    if log:
        log.line(f"{ref}: {fetched.title[:60]}", phase="P0")
        log.cont(f"{len(fetched.comments)} comment(s), "
                 f"{len(text)} characters of issue text")
    return text, repo
