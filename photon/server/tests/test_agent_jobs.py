"""Agent jobs — the rules between a harness plan and a pull request.

No database, queue or model: everything that decides what the agent may do
is a pure function of the job and the harness's own artifacts, and that is
what is tested here. The two properties that matter most are that narrowing
can only REMOVE files, and that nothing reaches a PR unless it was verified
AND stayed inside what the owner approved.
"""
from __future__ import annotations

import hashlib
import hmac
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import agent_jobs as rules  # noqa: E402

ARTIFACTS = {
    "rootcause": {"statement": "bucket bounds overlap at 50", "confidence": 0.8,
                  "classification": "logic"},
    "scope": {"fix_description": "make the bounds half-open",
              "files_to_change": [{"path": "src/lib/stats.js", "intent": "fix bounds"},
                                  {"path": "src/lib/format.js"}],
              "files_must_not_change": ["test/stats.test.js"],
              "risks": ["callers relying on the overlap"],
              "estimated_lines_changed": 4},
}


def test_summarize_plan_keeps_what_a_person_decides_on():
    s = rules.summarize_plan(ARTIFACTS)
    assert s["files"] == ["src/lib/stats.js", "src/lib/format.js"]
    assert s["must_not"] == ["test/stats.test.js"]
    assert s["root_cause"].startswith("bucket bounds")
    assert rules.plan_is_actionable(s) is None


def test_a_plan_with_no_file_or_no_cause_is_not_approvable():
    assert rules.plan_is_actionable(rules.summarize_plan({})) is not None
    no_cause = rules.summarize_plan({"scope": ARTIFACTS["scope"]})
    assert "root cause" in rules.plan_is_actionable(no_cause)


def test_plan_comment_lists_files_and_the_commands():
    body = rules.render_plan_comment("t", rules.summarize_plan(ARTIFACTS),
                                     "http://c/agent?job=1", "priya")
    assert "@priya" in body and "`src/lib/stats.js` — fix bounds" in body
    for cmd in ("/approve", "/narrow", "/reject"):
        assert cmd in body
    assert "Nothing has been changed yet" in body


# ── commands ─────────────────────────────────────────────────────────────

def test_parse_command():
    assert rules.parse_command("/approve").kind == "approve"
    c = rules.parse_command("looks good\n/narrow `src/a.js`, src/b.js")
    assert c.kind == "narrow" and c.files == ["src/a.js", "src/b.js"]
    c = rules.parse_command("/reject wrong root cause")
    assert c.kind == "reject" and c.note == "wrong root cause"
    assert rules.parse_command("please approve this") is None


def test_quoting_a_command_is_not_deciding():
    assert rules.parse_command("> /approve\nI am not sure about this") is None


# ── scope ────────────────────────────────────────────────────────────────

def test_approve_as_planned():
    scope = rules.approved_scope(rules.summarize_plan(ARTIFACTS))
    assert scope["files"] == ["src/lib/stats.js", "src/lib/format.js"]
    assert scope["must_not"] == ["test/stats.test.js"]


def test_narrowing_moves_dropped_files_to_must_not():
    scope = rules.approved_scope(rules.summarize_plan(ARTIFACTS),
                                 ["./src/lib/stats.js"], "keep the API")
    assert scope["files"] == ["src/lib/stats.js"]
    assert "src/lib/format.js" in scope["must_not"]
    assert scope["constraints"] == "keep the API"


def test_narrowing_cannot_add_a_file():
    with pytest.raises(ValueError, match="not in the plan"):
        rules.approved_scope(rules.summarize_plan(ARTIFACTS), ["src/other.js"])


# ── after the fix ────────────────────────────────────────────────────────

def _run(code=0, ok=True, overall="HIGH", status="done"):
    return {"status": status, "exit_code": code, "outcome": "X",
            "scope_check": {"ok": ok, "outside": [] if ok else ["README.md"]},
            "artifacts": {"confidence": {"overall": overall, "score": 6,
                                         "blocking": []}}}


def test_decide():
    assert rules.decide(_run())[0] == "pr"
    assert rules.decide(_run(code=2, overall="MEDIUM"))[0] == "draft_pr"
    assert rules.decide(_run(code=2, overall="LOW"))[0] == "escalate"
    assert rules.decide(_run(code=3))[0] == "escalate"
    assert rules.decide(_run(status="failed"))[0] == "escalate"


def test_a_verified_fix_outside_scope_still_escalates():
    action, why = rules.decide(_run(ok=False))
    assert action == "escalate" and "README.md" in why


def test_pr_body_credits_the_owner():
    body = rules.pr_body(title="t", ticket_ref="acme/app#7",
                         ticket_url="https://github.com/acme/app/issues/7",
                         owner_login="priya", summary=rules.summarize_plan(ARTIFACTS),
                         scope={"files": ["src/lib/stats.js"]}, run=_run(), why="verified")
    assert "on behalf of @priya" in body
    assert "[acme/app#7](https://github.com/acme/app/issues/7)" in body


def test_branch_and_refs():
    assert rules.branch_name("abcdef123456", "Fix: buckets overlap!") == \
        "photon/abcdef12-fix-buckets-overlap"
    assert rules.parse_github_ref("acme/app#12") == ("acme/app", 12)
    assert rules.parse_github_ref("ENG-42") is None


# ── webhook ──────────────────────────────────────────────────────────────

def test_webhook_signature():
    from app.routers.agent_jobs import signature_ok
    body = b'{"action":"created"}'
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert signature_ok("s3cret", body, good)
    assert not signature_ok("s3cret", body, good[:-1] + "0")
    assert not signature_ok("", body, good)          # unconfigured never passes
    assert not signature_ok("s3cret", body, None)


# ── Linear ───────────────────────────────────────────────────────────────

def test_linear_signature_and_helpers():
    from app.services import linear_tickets as lt
    body = b'{"type":"Issue"}'
    good = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert lt.signature_ok("s3cret", body, good)
    assert not lt.signature_ok("s3cret", body, "sha256=" + good)   # Linear sends bare hex
    assert not lt.signature_ok("", body, good)
    assert lt.labels_of({"labels": {"nodes": [{"name": "photon-fix"}, {"name": "bug"}]}}) == ["photon-fix", "bug"]
    # A personal key comments as its owner — so the agent always signs.
    assert lt.signed("Priya", "plan").startswith("_Photon — agent for Priya_")
