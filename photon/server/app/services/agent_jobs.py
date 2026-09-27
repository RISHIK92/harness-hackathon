"""Agent jobs — the rules around a ticket fix, kept apart from the transport.

A member's agent takes a ticket, asks the harness for a plan, shows that plan
to the member (on the ticket and in the console), and only after the member
approves or narrows it asks the harness for the fix. Everything here is a
pure function of the job and the harness's own artifacts, so it is tested
without a database, a queue or a model.

The member is the point of contact throughout. The agent never widens what
was approved, never merges, and every outcome it cannot stand behind comes
back to the member as an escalation with the reason attached.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

SUCCESS, PARTIAL = 0, 2


# ── the plan ─────────────────────────────────────────────────────────────

def summarize_plan(artifacts: dict) -> dict:
    """The part of a plan run a person needs to decide on it."""
    root = artifacts.get("rootcause") or {}
    scope = artifacts.get("scope") or {}
    files = [f.get("path") for f in scope.get("files_to_change") or []
             if isinstance(f, dict) and f.get("path")]
    intents = {f["path"]: f.get("intent", "") for f in
               scope.get("files_to_change") or [] if isinstance(f, dict) and f.get("path")}
    return {
        "root_cause": root.get("statement") or "",
        "root_cause_confidence": root.get("confidence"),
        "classification": root.get("classification"),
        "fix_description": scope.get("fix_description") or "",
        "files": files,
        "file_intents": intents,
        "must_not": list(scope.get("files_must_not_change") or []),
        "risks": list(scope.get("risks") or []),
        "estimated_lines": scope.get("estimated_lines_changed"),
    }


def plan_is_actionable(summary: dict) -> Optional[str]:
    """None when a person can approve this plan; otherwise why not."""
    if not summary.get("files"):
        return "the harness could not name a file to change"
    if not summary.get("root_cause"):
        return "the harness found no evidenced root cause"
    return None


def render_plan_comment(title: str, summary: dict, console_url: str,
                        owner_login: Optional[str]) -> str:
    who = f"@{owner_login}" if owner_login else "the assignee"
    lines = [
        f"**Photon — proposed fix plan** (acting for {who})",
        "",
        f"**Root cause:** {summary.get('root_cause') or '—'}",
        "",
        f"**Change:** {summary.get('fix_description') or '—'}",
        "",
        "**Files it will change:**",
    ]
    intents = summary.get("file_intents") or {}
    for path in summary.get("files") or []:
        note = intents.get(path)
        lines.append(f"- `{path}`" + (f" — {note}" if note else ""))
    if summary.get("must_not"):
        lines += ["", "**Will not touch:** " +
                  ", ".join(f"`{p}`" for p in summary["must_not"])]
    if summary.get("risks"):
        lines += ["", "**Risks:**"] + [f"- {r}" for r in summary["risks"]]
    lines += [
        "",
        "Nothing has been changed yet. Reply to decide:",
        "- `/approve` — fix exactly as planned",
        "- `/narrow path/a.py, path/b.py` — fix only those files",
        "- `/reject <reason>` — drop it",
        "",
        f"Or review it in the console: {console_url}",
    ]
    return "\n".join(lines)


# ── the owner's decision ─────────────────────────────────────────────────

@dataclass
class Command:
    kind: str                         # approve | narrow | reject
    files: list = field(default_factory=list)
    note: str = ""


_COMMAND = re.compile(r"^\s*/(approve|narrow|reject)\b[ \t]*(.*)$", re.I | re.M)


def parse_command(text: str) -> Optional[Command]:
    """The first /command on its own line. Quoted replies ("> /approve")
    do not count: quoting someone is not deciding."""
    for m in _COMMAND.finditer(text or ""):
        line_start = (text or "").rfind("\n", 0, m.start()) + 1
        if (text or "")[line_start:m.start()].strip().startswith(">"):
            continue
        kind, rest = m.group(1).lower(), m.group(2).strip()
        if kind == "narrow":
            files = [p.strip().strip("`") for p in re.split(r"[,\s]+", rest) if p.strip()]
            return Command("narrow", files=files)
        return Command(kind, note=rest)
    return None


def approved_scope(summary: dict, files: Optional[list] = None,
                   constraints: str = "") -> dict:
    """The scope the fix run is bound to.

    Narrowing may only REMOVE files from the plan. Adding one would be the
    member approving a change nobody planned or explained, through a text
    box — if the plan is wrong, reject it and re-plan.
    """
    planned = list(summary.get("files") or [])
    chosen = planned if not files else [f.lstrip("./") for f in files]
    unknown = [f for f in chosen if f not in planned]
    if unknown:
        raise ValueError("not in the plan: " + ", ".join(unknown))
    if not chosen:
        raise ValueError("the scope names no file")
    dropped = [f for f in planned if f not in chosen]
    return {
        "files": chosen,
        "must_not": list(summary.get("must_not") or []) + dropped,
        "constraints": (constraints or "").strip(),
    }


# ── after the fix ────────────────────────────────────────────────────────

def decide(run: dict) -> tuple[str, str]:
    """("pr" | "draft_pr" | "escalate", why).

    A pull request goes up only when the harness verified the fix AND the
    diff stayed inside what the member approved. A partial fix with the hard
    gates green goes up as a draft; everything else is the member's call.
    """
    if run.get("status") != "done":
        return "escalate", run.get("error") or f"the run {run.get('status')}"
    check = run.get("scope_check") or {}
    if not check.get("ok"):
        outside = (check.get("outside") or []) + (check.get("forbidden") or [])
        return "escalate", "the fix changed files outside the approved scope: " + ", ".join(outside)
    conf = (run.get("artifacts") or {}).get("confidence") or {}
    overall = conf.get("overall")
    code = run.get("exit_code")
    if code == SUCCESS:
        return "pr", "verified"
    if code == PARTIAL and overall in ("HIGH", "MEDIUM"):
        return "draft_pr", "partially verified: " + ", ".join(conf.get("blocking") or ["a soft condition failed"])
    return "escalate", f"the harness did not produce a verified fix ({run.get('outcome')})"


def branch_name(job_id: str, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "fix").lower()).strip("-")[:40]
    return f"photon/{job_id[:8]}-{slug or 'fix'}"


def pr_body(*, title: str, ticket_ref: Optional[str], ticket_url: Optional[str],
            owner_login: Optional[str], summary: dict, scope: dict,
            run: dict, why: str) -> str:
    who = f"@{owner_login}" if owner_login else "a workspace member"
    conf = (run.get("artifacts") or {}).get("confidence") or {}
    ticket = f"[{ticket_ref}]({ticket_url})" if ticket_url and ticket_ref else (ticket_ref or "—")
    lines = [
        f"Opened by Photon on behalf of {who}, who approved the plan below.",
        "",
        f"**Ticket:** {ticket}",
        f"**Root cause:** {summary.get('root_cause') or '—'}",
        f"**Change:** {summary.get('fix_description') or '—'}",
        f"**Approved scope:** " + ", ".join(f"`{f}`" for f in scope.get("files") or []),
        f"**Verification:** {why}; confidence {conf.get('overall', '—')} "
        f"({conf.get('score', '?')}/6)",
    ]
    if scope.get("constraints"):
        lines += ["", f"**Reviewer notes:** {scope['constraints']}"]
    report = (run.get("artifacts") or {}).get("run_report")
    if report:
        lines += ["", "<details><summary>Harness report</summary>", "",
                  report[:40_000], "", "</details>"]
    return "\n".join(lines)


# ── GitHub tickets ───────────────────────────────────────────────────────

_GH_REF = re.compile(r"^([\w.-]+/[\w.-]+)#(\d+)$")


def parse_github_ref(ref: Optional[str]) -> Optional[tuple[str, int]]:
    m = _GH_REF.match((ref or "").strip())
    return (m.group(1), int(m.group(2))) if m else None


def post_github_comment(token: str, ticket_ref: str, body: str) -> Optional[str]:
    parsed = parse_github_ref(ticket_ref)
    if not parsed:
        return None
    slug, number = parsed
    resp = httpx.post(
        f"https://api.github.com/repos/{slug}/issues/{number}/comments",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"},
        json={"body": body}, timeout=15.0)
    resp.raise_for_status()
    return resp.json().get("html_url")


def issue_text(title: str, body: str, context: str = "") -> str:
    parts = [title.strip(), "", (body or "").strip()]
    if context.strip():
        parts += ["", "## Context from the team", context.strip()]
    return "\n".join(parts).strip() + "\n"
