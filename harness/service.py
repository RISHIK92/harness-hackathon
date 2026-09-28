"""An HTTP front door for the harness, for callers that are programs.

    python -m harness.service            # 127.0.0.1:8765

`make run` is how a person drives the harness; this is how another system
does -- a ticket queue, a chat bot, an agent acting for an engineer. It adds
nothing to a run. Every run is the unchanged CLI (`python -m harness`) in a
subprocess, with the same phases, budgets, verification and report, so what
a caller gets back is exactly what `make run` would have produced.

What it adds is the part around a run that a caller cannot do for itself:

    plan      P0-P2 only (HARNESS_DRY_RUN). Root cause and the change plan,
              nothing written -- the thing a human approves or narrows.
    fix       the full run, in the plan's checkout, with the approved scope
              written into the issue. The diff is then checked against that
              scope here, because a plan the model re-derived is not the plan
              a person approved.
    publish   commit, push and open the pull request with a token the CALLER
              supplies. The harness never holds a standing credential, and
              its own push/PR/comment consent stays off for every run.
              The change is taken out of the run's checkout as a patch and
              committed from a fresh clone: the checkout is where the
              repository's own code ran, and nothing it left behind (hooks,
              config) is anywhere near the token.

Endpoints (JSON in, JSON out):

    GET    /v1/health
    GET    /v1/runs
    POST   /v1/runs                   {mode, issue, repo | from_run, scope?}
    GET    /v1/runs/{id}
    GET    /v1/runs/{id}/log?tail=N
    POST   /v1/runs/{id}/publish      {token, branch, title, body, ...}
    DELETE /v1/runs/{id}              cancel

Environment:

    HARNESS_SERVICE_TOKEN    bearer token callers must send. Without one the
                             service refuses to listen beyond loopback.
    HARNESS_SERVICE_HOST     default 127.0.0.1
    HARNESS_SERVICE_PORT     default 8765
    HARNESS_SERVICE_HOME     default ./.harness/service
    HARNESS_SERVICE_WORKERS  concurrent runs, default 2
    HARNESS_SERVICE_ALLOW_LOCAL
                             1 lets callers name a local `repo.path` (or any
                             non-https URL). Local development only: it lets
                             any caller read any repository on this machine.

AI_API_KEY and the other HARNESS_* settings are read from the service's own
environment and passed through to every run, exactly as `make run` would.
Each run is marked HARNESS_SERVICE_RUN=1, which keeps the CLI from reading
a repository out of the ticket text and from publishing anything itself.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import exits
from .github import auth_env
from .repo.paths import norm
from .repo.workspace import scaffolding
from .verify.runner import is_secret

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ("issue.json", "baseline.json", "rootcause.json", "scope.json",
             "verification.json", "confidence.json", "diff.patch",
             "run_report.md", "error.log")
MODES = ("plan", "fix")
MAX_BODY = 2_000_000

# The caller decides which of these a run may override. Anything else --
# above all AI_API_KEY and the consent settings -- is the service's to set.
# Not HARNESS_TEST_CMD or HARNESS_LINT_CMD: each is a shell command the run
# executes on this machine, so letting a caller set one was letting any
# caller run anything here.
OVERRIDABLE = {"HARNESS_MODEL", "HARNESS_CHEAP_MODEL", "HARNESS_PROVIDER",
               "HARNESS_TIER", "HARNESS_TIME_BUDGET", "HARNESS_TOKEN_BUDGET",
               "HARNESS_MAX_CYCLES", "HARNESS_DOCKER"}

# The service's own git, on a checkout a repository's code has run in.
# Hooks, fsmonitor and credential helpers are how a checkout (or the
# operator's machine) runs code or hands out a credential when git touches
# it; none is wanted. An empty `credential.helper` resets the list, so the
# only credential any command carries is the caller's token.
SAFE_GIT = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
            "-c", "credential.helper=", "-c", "core.quotePath=false")

# What git needs from the service's environment, and nothing else: not
# AI_API_KEY, not the service token.
GIT_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "USER",
           "LOGNAME", "SSL_CERT_FILE", "SSL_CERT_DIR", "GIT_SSL_CAINFO",
           "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
           "NO_PROXY", "no_proxy", "GIT_EXEC_PATH", "SYSTEMROOT")

BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


class RequestError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


# -- records -----------------------------------------------------------------

@dataclass
class Run:
    id: str
    mode: str
    issue: str
    repo: dict = field(default_factory=dict)      # url/path/ref -- never a token
    scope: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)
    from_run: str | None = None
    status: str = "queued"      # queued running done failed cancelled
    exit_code: int | None = None
    outcome: str | None = None
    error: str | None = None
    checkout: str | None = None
    base: str | None = None       # the commit the checkout started from
    scope_check: dict | None = None
    published: dict | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def public(self, home: Path, detail: bool = True) -> dict:
        d = asdict(self)
        d.pop("env", None)
        d.pop("checkout", None)
        if detail:
            d["artifacts"] = read_artifacts(home / "runs" / self.id / "artifacts")
        return d


def read_artifacts(folder: Path) -> dict:
    out: dict = {}
    if not folder.is_dir():
        return out
    for name in ARTIFACTS:
        p = folder / name
        if not p.is_file():
            continue
        text = p.read_text("utf-8", errors="replace")
        key = name.rsplit(".", 1)[0]
        if name.endswith(".json") or name == "diff.patch":
            try:
                data = json.loads(text)
                out[key] = data.get("diff", "") if name == "diff.patch" else data
                continue
            except ValueError:
                pass
        out[key] = text
    return out


# -- git helpers ---------------------------------------------------------------

def git_env(extra: dict | None = None) -> dict:
    env = {k: os.environ[k] for k in GIT_ENV
           if k in os.environ and not is_secret(k, os.environ[k])}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.update(extra or {})
    return env


def git(args: list[str], cwd: Path | None, token: str | None = None,
        timeout: float = 600, env: dict | None = None,
        data: bytes | None = None,
        text: bool = True) -> subprocess.CompletedProcess:
    """One git command, with hooks off and a minimal environment. The token,
    when there is one, is in that environment only (`auth_env`)."""
    extra = {} if data is None else {"input": data}
    return subprocess.run(
        ["git", *SAFE_GIT, *args], cwd=cwd,
        env=git_env({**auth_env(token), **(env or {})}),
        stdin=None if data is not None else subprocess.DEVNULL,
        capture_output=True, timeout=timeout, **extra,
        **({"text": True, "errors": "replace"} if text else {}))


def _out(res: subprocess.CompletedProcess) -> str:
    raw = res.stderr or res.stdout or b""
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw


def make_patch(checkout: Path, base: str) -> tuple[bytes, list[str]]:
    """The run's change against `base`, as a binary patch, and its paths.

    This is what /publish commits and what the scope check judges, so the
    two can never disagree. `git diff HEAD` -- diff.patch -- cannot see a
    file the fix created; staging the tree into a scratch index can, and
    leaves the checkout's own index alone for the next fix run. Diffing
    against the commit the checkout STARTED from means a commit made in it
    meanwhile is part of the change rather than a reason it is "empty".

    It reads a checkout the repository's code ran in, so it runs with no
    token in its environment and with hooks off (`git`). Untracked files
    are the fix's new files, less the run's by-products (`scaffolding`).
    """
    with tempfile.TemporaryDirectory(prefix="harness-patch-") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index"),
               "GIT_LITERAL_PATHSPECS": "1"}
        steps = (["read-tree", base],
                 ["add", "-u", "--", "."],)
        for args in steps:
            res = git(args, checkout, env=env, timeout=300, text=False)
            if res.returncode != 0:
                raise RuntimeError(f"git {args[0]} failed: {_out(res)[-300:]}")
        res = git(["ls-files", "-z", "--others", "--exclude-standard"],
                  checkout, env=env, timeout=300, text=False)
        new = [p for p in res.stdout.decode("utf-8", "surrogateescape")
               .split("\0") if p and not scaffolding(p)]
        if new:
            res = git(["add", "--pathspec-from-file=-",
                       "--pathspec-file-nul"], checkout, env=env,
                      timeout=300, text=False, data="\0".join(new).encode(
                          "utf-8", "surrogateescape"))
            if res.returncode != 0:
                raise RuntimeError(f"git add failed: {_out(res)[-300:]}")
        diff = ["diff", "--cached", "--no-renames", "--no-ext-diff",
                "--no-textconv"]
        patch = git([*diff, "--binary", base, "--"], checkout, env=env,
                    timeout=300, text=False)
        names = git([*diff, "--name-only", "-z", base, "--"], checkout,
                    env=env, timeout=300, text=False)
        if patch.returncode != 0 or names.returncode != 0:
            raise RuntimeError(f"git diff failed: "
                               f"{_out(patch if patch.returncode else names)[-300:]}")
    paths = [p for p in names.stdout.decode("utf-8", "surrogateescape")
             .split("\0") if p]
    return patch.stdout, paths


def github_slug(url: str) -> str | None:
    m = re.match(r"^(?:https?://[^/]+/|git@[^:]+:)([^/]+)/([^/]+?)(?:\.git)?/?$",
                 (url or "").strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def changed_paths(diff: str) -> list[str]:
    paths: list[str] = []
    for line in (diff or "").splitlines():
        m = re.match(r"^diff --git a/(.+?) b/(.+)$", line)
        if m:
            for p in (m.group(1), m.group(2)):
                if p not in paths:
                    paths.append(p)
    return paths


def check_scope(diff: str, scope: dict, paths: list[str] | None = None) -> dict:
    """Is every changed file inside what was approved?

    An empty approval list means the caller approved the plan as-is and
    asked for no narrowing, so only the must-not list applies. `paths`,
    when given, is the change's file list (`make_patch`), which includes
    new files; otherwise it is read from `diff`.

    Paths are canonicalised, not `lstrip("./")`-ed: that strips characters,
    so an approved `.github/workflows/ci.yml` became `github/...` and could
    never match the file it named.
    """
    changed = [norm(p) for p in paths] if paths is not None \
        else changed_paths(diff)
    allowed = {norm(p) for p in scope.get("files") or []}
    forbidden = {norm(p) for p in scope.get("must_not") or []}
    outside = [p for p in changed if allowed and p not in allowed]
    touched_forbidden = [p for p in changed if p in forbidden]
    return {"ok": not outside and not touched_forbidden,
            "changed": changed, "outside": outside,
            "forbidden": touched_forbidden}


def scoped_issue(issue: str, scope: dict) -> str:
    if not scope:
        return issue
    parts = [issue.rstrip(), "", "## Approved scope",
             "A reviewer approved the change below. Stay inside it."]
    if scope.get("files"):
        parts.append("Change only: " + ", ".join(scope["files"]))
    if scope.get("must_not"):
        parts.append("Do not change: " + ", ".join(scope["must_not"]))
    if scope.get("constraints"):
        parts += ["", "Reviewer notes:", str(scope["constraints"]).strip()]
    return "\n".join(parts) + "\n"


# -- the service -----------------------------------------------------------------

class Service:
    def __init__(self, home: Path, workers: int = 2,
                 command: list[str] | None = None,
                 allow_local: bool | None = None) -> None:
        self.home = Path(home).resolve()
        (self.home / "runs").mkdir(parents=True, exist_ok=True)
        self.command = command or [sys.executable, "-m", "harness"]
        # A local path is any repository on this machine, readable by any
        # caller holding the service token. Development only, and only when
        # the operator says so.
        self.allow_local = allow_local if allow_local is not None else \
            os.environ.get("HARNESS_SERVICE_ALLOW_LOCAL", "").strip().lower() \
            in ("1", "true", "yes", "on")
        self.runs: dict[str, Run] = {}
        self.procs: dict[str, subprocess.Popen] = {}
        self.publishing: set[str] = set()
        self.lock = threading.Lock()
        self.slots = threading.Semaphore(max(1, workers))
        self._load()

    # -- state ------------------------------------------------------------
    def _load(self) -> None:
        """Runs survive a restart as records; a run in flight does not."""
        for state in (self.home / "runs").glob("*/state.json"):
            try:
                run = Run(**json.loads(state.read_text("utf-8")))
            except (ValueError, TypeError):
                continue
            if run.status in ("queued", "running"):
                run.status, run.error = "failed", "the service restarted"
                run.finished_at = time.time()
            self.runs[run.id] = run
            self._save(run)

    def _save(self, run: Run) -> None:
        folder = self.home / "runs" / run.id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "state.json").write_text(
            json.dumps(asdict(run), indent=2), encoding="utf-8")

    def get(self, run_id: str) -> Run:
        run = self.runs.get(run_id)
        if not run:
            raise RequestError(404, "no such run")
        return run

    # -- create -----------------------------------------------------------
    def create(self, body: dict) -> Run:
        mode = body.get("mode", "plan")
        if mode not in MODES:
            raise RequestError(400, f"mode must be one of {MODES}")
        issue = (body.get("issue") or "").strip()
        from_run = body.get("from_run")
        repo = body.get("repo") or {}
        if from_run:
            base = self.get(from_run)
            if base.status != "done" or not base.checkout:
                raise RequestError(409, "from_run has no finished checkout")
            issue = issue or base.issue
            repo = base.repo
        else:
            self._check_repo(repo)
        if not issue:
            raise RequestError(400, "issue is required")
        env = {k: str(v) for k, v in (body.get("env") or {}).items()
               if k in OVERRIDABLE}
        run = Run(id=uuid.uuid4().hex[:12], mode=mode, issue=issue,
                  repo={k: repo[k] for k in ("url", "path", "ref") if repo.get(k)},
                  scope=body.get("scope") or {}, env=env, from_run=from_run)
        with self.lock:
            # Checked and claimed in one step. Checked outside the lock, two
            # requests arriving together both saw the checkout free, and two
            # fix runs edited one working tree at once.
            if from_run and any(r.from_run == from_run
                                and r.status in ("queued", "running")
                                for r in self.runs.values()):
                raise RequestError(409, "a run already uses that checkout")
            self.runs[run.id] = run
            self._save(run)
        token = body.get("token")      # used for the clone, then dropped
        threading.Thread(target=self._execute, args=(run, token),
                         daemon=True, name=f"run-{run.id}").start()
        return run

    def _check_repo(self, repo: dict) -> None:
        """A repository a caller may name: an https URL, and nothing else.

        A local path clones whatever is on this machine; `file://`, `ext::`
        and a bare path do the same by another spelling; and the caller's
        token is sent as an https header, so https is the one transport
        whose credential is the caller's rather than this machine's.
        HARNESS_SERVICE_ALLOW_LOCAL=1 lifts this, for local development.
        """
        if not isinstance(repo, dict):
            raise RequestError(400, "repo must be an object")
        url, path = str(repo.get("url") or ""), repo.get("path")
        if not (url or path):
            raise RequestError(400, "repo.url is required")
        if str(repo.get("ref") or "").startswith("-"):
            raise RequestError(400, "repo.ref is not a git reference")
        if re.match(r"^[a-z][\w+.-]*://[^/]*@", url, re.I):
            raise RequestError(400, "repo.url carries a credential; send "
                                    "the token as `token` instead")
        if self.allow_local:
            return
        if path:
            raise RequestError(400, "repo.path is not accepted: name the "
                                    "repository by its https URL")
        if not re.match(r"^https://[^/\s]+/\S+$", url):
            raise RequestError(400, "repo.url must be an https URL")

    # -- execute ----------------------------------------------------------
    def _execute(self, run: Run, token: str | None) -> None:
        with self.slots:
            if run.status == "cancelled":
                return
            run.status, run.started_at = "running", time.time()
            self._save(run)
            folder = self.home / "runs" / run.id
            try:
                checkout = self._checkout(run, folder, token)
                run.checkout = str(checkout)
                self._save(run)
                code = self._invoke(run, folder, checkout)
                self._collect(run, folder, checkout)
                run.exit_code = code
                run.outcome = exits.REASON.get(code, str(code))
                if run.mode == "fix":
                    run.scope_check = self._scope_check(run, checkout)
                if run.status != "cancelled":
                    run.status = "done"
            except Exception as exc:          # a run must always finish
                if run.status != "cancelled":
                    run.status, run.error = "failed", str(exc)[:2000]
            finally:
                run.finished_at = time.time()
                self.procs.pop(run.id, None)
                self._save(run)

    def _scope_check(self, run: Run, checkout: Path) -> dict:
        """The scope verdict on exactly what /publish would commit.

        Judged on `make_patch`, not on diff.patch: that is `git diff HEAD`,
        which does not list a file the fix created, so a new file anywhere
        passed the scope check and was then published. The patch's hash is
        kept, and /publish refuses a checkout that no longer produces it.
        """
        try:
            patch, paths = make_patch(checkout, self._base(run, checkout))
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "changed": [], "outside": [],
                    "forbidden": [], "error": str(exc)[:300]}
        verdict = check_scope("", run.scope, paths)
        verdict["patch_sha256"] = hashlib.sha256(patch).hexdigest()
        return verdict

    def _base(self, run: Run, checkout: Path) -> str:
        """The commit the run's checkout started from."""
        if run.base:
            return run.base
        if run.from_run and run.from_run in self.runs \
                and self.runs[run.from_run].base:
            return self.runs[run.from_run].base
        # A run recorded before the base was: its checkout's HEAD is the
        # best that is left.
        res = git(["rev-parse", "--verify", "HEAD^{commit}"], checkout)
        if res.returncode != 0:
            raise RuntimeError("the checkout has no base commit")
        return res.stdout.strip()

    def _checkout(self, run: Run, folder: Path, token: str | None) -> Path:
        if run.from_run:
            plan = self.get(run.from_run)
            run.base = plan.base
            return Path(plan.checkout)
        target = folder / "checkout"
        if target.exists():
            shutil.rmtree(target)
        source = run.repo.get("url") or str(Path(run.repo["path"]).resolve())
        # A local path is cloned too: a run edits files, and the caller's
        # own working copy is not ours to edit.
        res = git(["clone", "--quiet", source, str(target)], folder, token)
        if res.returncode != 0:
            raise RuntimeError(f"clone failed: {res.stderr.strip()[-400:]}")
        if run.repo.get("ref"):
            res = git(["checkout", "--quiet", run.repo["ref"]], target)
            if res.returncode != 0:
                raise RuntimeError(f"checkout {run.repo['ref']} failed: "
                                   f"{res.stderr.strip()[-400:]}")
        # Recorded before any of the repository's code runs: every later
        # patch is taken against this commit, whatever the checkout's HEAD
        # says by then.
        res = git(["rev-parse", "--verify", "HEAD^{commit}"], target)
        run.base = res.stdout.strip() if res.returncode == 0 else None
        return target

    def _invoke(self, run: Run, folder: Path, checkout: Path) -> int:
        env = {**os.environ, **run.env}
        for name in ("ISSUE", "ISSUE_FILE", "GITHUB_ISSUE", "GITHUB_PR",
                     "HARNESS_REPLAY", "HARNESS_DRY_RUN", "GITHUB_TOKEN",
                     "GH_TOKEN", "HARNESS_SERVICE_TOKEN"):
            env.pop(name, None)
        env.update({
            # The CLI's service mode: the repository is REPO_PATH and nothing
            # in the ticket text; no commit, push, pull request or comment
            # of its own -- publishing is /publish, from a clean clone.
            "HARNESS_SERVICE_RUN": "1",
            "ISSUE": scoped_issue(run.issue, run.scope if run.mode == "fix"
                                  else {}),
            "REPO_PATH": str(checkout),
            "HARNESS_NONINTERACTIVE": "1",
            # install only: the checkout is already here, and pushing,
            # opening a PR or commenting is what /publish is for.
            "HARNESS_AUTO": "install",
            "HARNESS_POST": "off",
            "PYTHONPATH": os.pathsep.join(
                p for p in (str(ROOT), env.get("PYTHONPATH", "")) if p),
        })
        if run.mode == "plan":
            env["HARNESS_DRY_RUN"] = "1"
        budget = int(env.get("HARNESS_TIME_BUDGET") or 1500)
        with open(folder / "output.log", "w", encoding="utf-8") as log:
            proc = subprocess.Popen(self.command, cwd=folder, env=env,
                                    stdin=subprocess.DEVNULL, stdout=log,
                                    stderr=subprocess.STDOUT,
                                    start_new_session=True)
            self.procs[run.id] = proc
            try:
                # The harness keeps its own budget; this only catches a run
                # that has stopped keeping it.
                return proc.wait(timeout=budget * 2 + 300)
            except subprocess.TimeoutExpired:
                self._kill(proc)
                raise RuntimeError("the run exceeded twice its time budget")

    def _collect(self, run: Run, folder: Path, checkout: Path) -> None:
        """Snapshot what the run wrote: a fix run in the same checkout
        overwrites .harness/run, and the plan must outlive it."""
        src = checkout / ".harness" / "run"
        dst = folder / "artifacts"
        dst.mkdir(parents=True, exist_ok=True)
        for name in ARTIFACTS:
            if (src / name).is_file():
                shutil.copy2(src / name, dst / name)

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired, PermissionError):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def cancel(self, run_id: str) -> Run:
        run = self.get(run_id)
        if run.status in ("done", "failed", "cancelled"):
            return run
        run.status, run.error = "cancelled", "cancelled by the caller"
        proc = self.procs.get(run_id)
        if proc:
            self._kill(proc)
        self._save(run)
        return run

    def log_tail(self, run_id: str, tail: int) -> str:
        self.get(run_id)
        p = self.home / "runs" / run_id / "output.log"
        if not p.is_file():
            return ""
        lines = p.read_text("utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max(1, min(tail, 5000)):])

    # -- publish ----------------------------------------------------------
    def publish(self, run_id: str, body: dict) -> dict:
        run = self.get(run_id)
        if run.published:
            return run.published
        if run.mode != "fix" or run.status != "done":
            raise RequestError(409, "only a finished fix run can be published")
        # The CLI's rule, on the run's own evidence. The exit code alone let
        # a PARTIAL run whose hard gates failed -- a broken test, a file
        # outside the plan -- go out as a pull request.
        art = read_artifacts(self.home / "runs" / run.id / "artifacts")
        refusal = exits.publish_refusal(run.exit_code, art.get("confidence"),
                                        art.get("verification"))
        if refusal:
            raise RequestError(409, f"the run cannot be published: {refusal}")
        if not (run.scope_check or {}).get("ok"):
            raise RequestError(409, "the change is outside the approved scope")
        token = body.get("token")
        branch = (body.get("branch") or "").strip()
        title = (body.get("title") or "").strip()
        if not (token and branch and title):
            raise RequestError(400, "token, branch and title are required")
        if not BRANCH.fullmatch(branch) or ".." in branch \
                or branch.endswith((".lock", "/")):
            raise RequestError(400, "branch is not a valid branch name")
        slug = github_slug(run.repo.get("url", ""))
        if not slug:
            raise RequestError(409, "the run's repository is not on GitHub")
        with self.lock:
            if run_id in self.publishing:
                raise RequestError(409, "this run is already being published")
            self.publishing.add(run_id)
        try:
            result = self._publish(run, body, token, branch, title, slug)
        finally:
            with self.lock:
                self.publishing.discard(run_id)
        run.published = result
        self._save(run)
        return result

    def _publish(self, run: Run, body: dict, token: str, branch: str,
                 title: str, slug: str) -> dict:
        """Commit and push the run's change from a clone made for it.

        The run's checkout is where the repository's own code ran -- its
        install scripts, its tests, a model-written test -- so it can hold
        a planted `.git/hooks/pre-commit`, a `core.fsmonitor` command, a
        `url.*.insteadOf` pointing the push somewhere else. Committing and
        pushing there ran all of it with the caller's write token in the
        environment. Now the checkout is only READ, without the token
        (`make_patch`); the commit and the push happen in a fresh clone of
        the remote, where nothing of the run's exists but the patch.
        """
        checkout = Path(run.checkout)
        try:
            base_commit = self._base(run, checkout)
            patch, _paths = make_patch(checkout, base_commit)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            raise RequestError(500, f"could not read the change: {exc}") \
                from None
        digest = hashlib.sha256(patch).hexdigest()
        if not patch.strip():
            raise RequestError(409, "the run left no change to publish")
        expected = (run.scope_check or {}).get("patch_sha256")
        if expected and expected != digest:
            # Another fix run shares this checkout. What would go out is not
            # what the scope check approved.
            raise RequestError(409, "the checkout changed after the run "
                                    "finished; publish the latest fix run")

        author = body.get("author") or {}
        name = author.get("name") or "harness"
        email = author.get("email") or "harness@local"
        message = title
        if body.get("trailers"):
            message += "\n\n" + "\n".join(body["trailers"])
        source = run.repo.get("url") or str(Path(run.repo["path"]).resolve())

        with tempfile.TemporaryDirectory(prefix="harness-publish-") as tmp:
            clone, patch_file = Path(tmp) / "repo", Path(tmp) / "change.patch"
            patch_file.write_bytes(patch)
            res = git(["clone", "--quiet", "--no-checkout", source,
                       str(clone)], Path(tmp), token)
            if res.returncode != 0:
                raise RequestError(502, f"clone for publishing failed: "
                                        f"{res.stderr.strip()[-300:]}")
            base = body.get("base") or self._default_branch(clone)
            for args in (["checkout", "-q", "-B", branch, base_commit],
                         ["apply", "--index", "--binary", str(patch_file)],
                         ["-c", f"user.name={name}", "-c", f"user.email={email}",
                          "commit", "-q", "-m", message]):
                res = git(args, clone)
                if res.returncode != 0:
                    raise RequestError(500, f"git {args[0]} failed: "
                                            f"{(res.stderr or res.stdout)[-300:]}")
            push = git(["push", "--force-with-lease", "origin",
                        f"HEAD:refs/heads/{branch}"], clone, token)
            if push.returncode != 0:
                raise RequestError(502, f"push failed: "
                                        f"{push.stderr.strip()[-300:]}")

        pr = github_request("POST", f"/repos/{slug}/pulls", token, {
            "title": title, "body": body.get("body") or "",
            "head": branch, "base": base,
            "draft": bool(body.get("draft"))})
        result = {"branch": branch, "base": base,
                  "url": pr.get("html_url"), "number": pr.get("number"),
                  "patch_sha256": digest}
        reviewers = [r for r in body.get("reviewers") or [] if r]
        if reviewers and pr.get("number"):
            try:
                github_request("POST", f"/repos/{slug}/pulls/{pr['number']}"
                                       f"/requested_reviewers", token,
                               {"reviewers": reviewers})
            except RequestError as exc:
                result["reviewer_error"] = str(exc)
        return result

    @staticmethod
    def _default_branch(repo: Path) -> str:
        res = git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], repo)
        ref = res.stdout.strip()
        return ref.split("/", 1)[1] if "/" in ref else "main"


def github_request(method: str, path: str, token: str, payload: dict) -> dict:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    api = (os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    req = Request(api + path, method=method,
                  data=json.dumps(payload).encode(),
                  headers={"Authorization": f"Bearer {token}",
                           "Accept": "application/vnd.github+json",
                           "Content-Type": "application/json",
                           "User-Agent": "harness-service"})
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RequestError(502, f"GitHub {exc.code}: {detail}") from None
    except URLError as exc:
        raise RequestError(502, f"GitHub unreachable: {exc.reason}") from None


# -- HTTP ------------------------------------------------------------------------

def make_handler(service: Service, token: str | None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "harness-service"

        def log_message(self, fmt, *args):      # stdout stays quiet
            pass

        def _reply(self, status: int, payload) -> None:
            body = json.dumps(payload, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self) -> bool:
            if not token:
                return True
            got = self.headers.get("Authorization", "")
            return hmac.compare_digest(got.encode(), f"Bearer {token}".encode())

        def _body(self) -> dict:
            size = int(self.headers.get("Content-Length") or 0)
            if size > MAX_BODY:
                raise RequestError(413, "request body too large")
            raw = self.rfile.read(size) if size else b"{}"
            try:
                data = json.loads(raw or b"{}")
            except ValueError:
                raise RequestError(400, "body is not JSON") from None
            if not isinstance(data, dict):
                raise RequestError(400, "body must be a JSON object")
            return data

        def _dispatch(self, method: str) -> None:
            try:
                url = urlparse(self.path)
                parts = [p for p in url.path.split("/") if p]
                if parts == ["v1", "health"] and method == "GET":
                    return self._reply(200, {"ok": True, "runs": len(service.runs)})
                if not self._authorised():
                    return self._reply(401, {"error": "unauthorised"})
                if parts == ["v1", "runs"]:
                    if method == "GET":
                        runs = sorted(service.runs.values(),
                                      key=lambda r: r.created_at, reverse=True)
                        return self._reply(200, [r.public(service.home, False)
                                                 for r in runs])
                    if method == "POST":
                        run = service.create(self._body())
                        return self._reply(202, run.public(service.home, False))
                if len(parts) == 3 and parts[:2] == ["v1", "runs"]:
                    if method == "GET":
                        return self._reply(200, service.get(parts[2])
                                           .public(service.home))
                    if method == "DELETE":
                        return self._reply(200, service.cancel(parts[2])
                                           .public(service.home, False))
                if len(parts) == 4 and parts[:2] == ["v1", "runs"]:
                    if parts[3] == "log" and method == "GET":
                        tail = int((parse_qs(url.query).get("tail") or ["200"])[0])
                        return self._reply(200, {"log": service.log_tail(parts[2], tail)})
                    if parts[3] == "publish" and method == "POST":
                        return self._reply(200, service.publish(parts[2],
                                                                self._body()))
                return self._reply(404, {"error": "not found"})
            except RequestError as exc:
                return self._reply(exc.status, {"error": str(exc)})
            except Exception as exc:
                return self._reply(500, {"error": f"internal: {exc}"})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_DELETE(self):
            self._dispatch("DELETE")

    return Handler


def serve(host: str, port: int, service: Service,
          token: str | None) -> ThreadingHTTPServer:
    if not token and host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("HARNESS_SERVICE_TOKEN is required to listen on "
                         f"{host}; without it only loopback is allowed")
    return ThreadingHTTPServer((host, port), make_handler(service, token))


def main() -> int:
    host = os.environ.get("HARNESS_SERVICE_HOST", "127.0.0.1")
    port = int(os.environ.get("HARNESS_SERVICE_PORT", "8765"))
    home = Path(os.environ.get("HARNESS_SERVICE_HOME")
                or Path.cwd() / ".harness" / "service")
    workers = int(os.environ.get("HARNESS_SERVICE_WORKERS", "2"))
    token = os.environ.get("HARNESS_SERVICE_TOKEN") or None
    if not os.environ.get("AI_API_KEY"):
        print("warning: AI_API_KEY is not set; every run will exit "
              "CONFIG_ERROR until it is", file=sys.stderr)
    httpd = serve(host, port, Service(home, workers), token)
    print(f"harness service on http://{host}:{port} "
          f"({'token required' if token else 'no token, loopback only'})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
