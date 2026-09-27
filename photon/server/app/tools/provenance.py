"""explain_why — the S2 tool. Joins code -> commit -> ticket -> PR -> Slack
thread so the "why" behind a piece of code is traceable to the human
decision that produced it, not just to the code itself.

Every hop is returned as its own evidence item. If a hop can't be found,
the chain stops there and the tool returns what it has plus a note — it
never lets an LLM bridge the gap with a plausible-sounding invention.
"""
from __future__ import annotations

import re

import structlog

from app.seed.loader import load_commits, load_prs, load_slack
from app.tools.code import search_code
from app.tools.evidence import make_evidence, tool_error, tool_result

log = structlog.get_logger()

_TICKET_RE = re.compile(r"\b([A-Z]{2,6}-\d+)\b")


def _looks_like_path(s: str) -> bool:
    return "/" in s or s.endswith(tuple([".py", ".js", ".ts", ".go", ".rs", ".java"]))


async def _locate_file(symbol_or_path: str, repo_id: str) -> tuple[str | None, list[dict]]:
    if _looks_like_path(symbol_or_path):
        return symbol_or_path, []

    result = await search_code(symbol_or_path, repo_id, top_k=3)
    if result["status"] != "ok" or not result["evidence"]:
        return None, []
    top = result["evidence"][0]
    file_path = top["locator"].split(":L", 1)[0]
    return file_path, [top]


def _commits_touching(file_path: str) -> list[dict]:
    basename_matches = [c for c in load_commits() if any(f == file_path or f.endswith(file_path) for f in c["files"])]
    return basename_matches


def _extract_ticket(commit: dict) -> str | None:
    m = _TICKET_RE.search(commit["message"])
    return m.group(1) if m else None


def _pr_for(ticket_id: str, commit_hash: str) -> dict | None:
    for pr in load_prs():
        if commit_hash in pr.get("commits", []):
            return pr
        if ticket_id and ticket_id in pr.get("description", "") + pr.get("title", ""):
            return pr
    return None


def _slack_thread_for(ticket_id: str | None, pr: dict | None) -> list[dict]:
    needle = (ticket_id or "") + " " + (pr["title"] if pr else "")
    needle = needle.strip().lower()
    if not needle:
        return []

    scored = []
    for m in load_slack():
        text_l = m["text"].lower()
        hit = 0
        if ticket_id and ticket_id.lower() in text_l:
            hit += 2
        for word in re.findall(r"[a-z]{4,}", needle):
            if word in text_l:
                hit += 1
        if hit:
            scored.append((hit, m))

    if not scored:
        return []

    scored.sort(key=lambda t: t[0], reverse=True)
    root_thread_ts = scored[0][1]["thread_ts"]
    thread = [m for m in load_slack() if m["thread_ts"] == root_thread_ts]
    thread.sort(key=lambda m: float(m["ts"]))
    return thread


async def explain_why(symbol_or_path: str, repo_id: str | None = None) -> dict:
    if not repo_id:
        # code -> commit -> ticket -> PR -> Slack is a per-repo chain (the
        # commit/PR fixtures are keyed by repo), so this can't fall back to
        # a workspace-wide search the way search_code/find_usages do.
        return tool_error("explain_why", "which repository? specify one of the known repos for this workspace")
    evidence: list[dict] = []

    file_path, code_evidence = await _locate_file(symbol_or_path, repo_id)
    evidence.extend(code_evidence)
    if not file_path:
        return tool_result(
            "explain_why", evidence, note=f"could not locate '{symbol_or_path}' in the code to start the chain"
        )
    if not code_evidence:
        # symbol_or_path was already a path; add a placeholder locator hop so the file itself is cited
        evidence.append(make_evidence("code", file_path, f"starting point: {file_path}", 1.0))

    commits = _commits_touching(file_path)
    if not commits:
        return tool_result(
            "explain_why", evidence, note=f"no commits found touching '{file_path}' — chain stops at code"
        )

    ticket_id = None
    chosen_commit = None
    for c in commits:
        t = _extract_ticket(c)
        if t:
            ticket_id = t
            chosen_commit = c
            break
    if not chosen_commit:
        chosen_commit = commits[0]

    evidence.append(
        make_evidence("commit", f"commit:{chosen_commit['hash']}", f"{chosen_commit['message']} ({chosen_commit['author']})", 0.9)
    )

    if not ticket_id:
        return tool_result(
            "explain_why", evidence, note="commit found but references no ticket id — chain stops at commit"
        )

    pr = _pr_for(ticket_id, chosen_commit["hash"])
    if not pr:
        return tool_result(
            "explain_why", evidence, note=f"found ticket {ticket_id} but no linked PR — chain stops at commit"
        )

    evidence.append(make_evidence("pr", f"pr:#{pr['number']}", f"#{pr['number']} {pr['title']} — {pr['description']}", 0.85))

    thread = _slack_thread_for(ticket_id, pr)
    if not thread:
        return tool_result("explain_why", evidence, note="found the PR but no linked Slack thread — chain stops at PR")

    for m in thread:
        locator = f"slack:#{m['channel']}:{m['ts']}"
        snippet = f"{m['user_name']} ({m['user_role']}): {m['text']}"
        evidence.append(make_evidence("slack", locator, snippet, 0.8))

    return tool_result("explain_why", evidence)
