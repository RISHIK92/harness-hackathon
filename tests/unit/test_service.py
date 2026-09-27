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
import json, os, pathlib, subprocess
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
        "HARNESS_NONINTERACTIVE", "GITHUB_TOKEN")}
(out / "rootcause.json").write_text(json.dumps({"env": env}))
if os.environ.get("HARNESS_DRY_RUN"):
    raise SystemExit(3)
target = "src/other.js" if "Change only: src/other.js" in issue else "src/stats.js"
if "TOUCH_README" in issue:
    target = "README.md"
(repo / target).parent.mkdir(parents=True, exist_ok=True)
(repo / target).write_text("fixed\n")
diff = subprocess.run(["git", "diff"], cwd=repo, capture_output=True,
                      text=True).stdout
if not diff:
    subprocess.run(["git", "add", "-N", target], cwd=repo)
    diff = subprocess.run(["git", "diff"], cwd=repo, capture_output=True,
                          text=True).stdout
(out / "diff.patch").write_text(json.dumps({"diff": diff}))
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
    return S.Service(tmp_path / "home", workers=2,
                     command=[sys.executable, str(script)])


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
                   "GITHUB_TOKEN": ""}
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
        fix.repo["url"] = "https://github.com/acme/app.git"
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
