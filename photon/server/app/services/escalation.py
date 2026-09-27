"""Escalation — the agent asking its person, live, in the middle of a meeting.

An agent attending for Priya will hit questions it cannot answer. Guessing is
off the table (the standing rule), and a flat "I don't know" in front of a
client is the other failure. What a person does instead is say "let me check
with Priya", keep the conversation going, and come back with her answer —
or, if she is unreachable, own it gracefully and promise a follow-up.

This module decides WHETHER to escalate and WHAT to say at each step. It is
pure: the router stores rows and the call-agent speaks, both on its word.

The bar is deliberately "could not answer AND it matters". Escalating every
abstention would ping the member for small talk the agent simply didn't
know; escalating confident answers would defeat having an agent at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

TIMEOUT_SECONDS = 60

# Ordered: the first topic that matches wins, and the commercial ones come
# first because they are where a wrong or missing answer costs the most.
TOPICS: list[tuple[str, re.Pattern]] = [
    ("pricing", re.compile(r"\b(pric\w*|cost\w*|discount\w*|quote|invoice\w*|billing|bill|refund\w*|contract\w*|licen[cs]\w*|seat\w*|plan\s+tier|renewal)\b", re.I)),
    # A bare "date" is not a timeline question ("filter by date range" is
    # technical) — only a date something is DUE on is.
    ("timeline", re.compile(r"\b(when will|by when|deadline|eta|(release|launch|delivery|go[- ]live|target) date|timeline|roadmap|launch\w*|ship\w*|next (week|month|quarter)|q[1-4])\b", re.I)),
    ("security", re.compile(r"\b(secur\w*|soc ?2|gdpr|hipaa|iso ?27001|complian\w*|encrypt\w*|pen ?test\w*|data (retention|residency)|legal|dpa|breach\w*)\b", re.I)),
    ("commitment", re.compile(r"\b(guarantee\w*|promise\w*|commit\w*|sla|uptime|can you (do|add|build|support)|will you|would you be able)\b", re.I)),
]
HIGH_STAKES = {"pricing", "timeline", "security", "commitment"}


@dataclass
class Assessment:
    escalate: bool
    topic: str
    importance: str        # high | medium | low
    reason: str


def topic_of(question: str) -> str:
    for name, pattern in TOPICS:
        if pattern.search(question or ""):
            return name
    return "technical"


def assess(question: str, result: dict, always_escalate: Optional[list] = None) -> Assessment:
    """Escalate when the agent could not stand behind an answer AND the
    question is worth a person's attention right now."""
    q = (question or "").strip()
    topic = topic_of(q)
    wants = [t.lower() for t in (always_escalate or []) if t]
    personal = any(t == topic or t in q.lower() for t in wants)

    abstained = bool(result.get("abstained"))
    low = (result.get("confidence") or "").lower() == "low"
    routed = bool(result.get("escalation"))

    if personal:
        # The member said so. Even a confident answer goes back to them —
        # "always ask me about pricing" is exactly that instruction.
        return Assessment(True, topic, "high", f"you asked to handle {topic} questions yourself")
    if not (abstained or low or routed):
        return Assessment(False, topic, "low", "answered with confidence")

    words = len(re.findall(r"\w+", q))
    if topic in HIGH_STAKES:
        importance = "high"
    elif words >= 5 or "?" in q:
        importance = "medium"
    else:
        importance = "low"
    if importance == "low":
        return Assessment(False, topic, importance, "too vague to be worth a ping")
    why = "abstained" if abstained else ("low confidence" if low else f"suggested routing to {result['escalation']}")
    return Assessment(True, topic, importance, why)


# ── what gets said ───────────────────────────────────────────────────────

def first_name(display_name: Optional[str], email: Optional[str] = None,
               login: Optional[str] = None) -> str:
    if display_name and display_name.strip():
        return display_name.strip().split()[0]
    local = (email or "").split("@")[0]
    local = re.sub(r"^\d+\+", "", local)          # GitHub noreply: 12345+login
    part = re.split(r"[._\-+]", local)[0] if local else (login or "")
    return part.capitalize() if part else "the team"


def _whom(name: str, company: bool) -> str:
    return "the team" if company else name


def holding_line(name: str, topic: str, company: bool = False, turn: int = 0) -> str:
    """Said instead of the abstention: honest that it is checking, and it
    hands the floor back so the conversation does not stall on one question."""
    who = _whom(name, company)
    if topic == "pricing":
        lines = [f"I'd rather not guess on pricing — let me check with {who} and come back to it in a minute. Meanwhile, anything else I can help with?",
                 f"Good one — pricing is {who}'s call, so I'm pinging them now. Happy to keep going while I wait."]
    elif topic == "timeline":
        lines = [f"I don't want to give you a date I can't stand behind — checking with {who} right now. What else is on your list?",
                 f"Let me confirm that timeline with {who} rather than guess. I'll come back to it shortly."]
    elif topic == "security":
        lines = [f"That's one I want exactly right, so I'm checking with {who} now. Shall we keep going in the meantime?"]
    else:
        lines = [f"Good question — I want to get that right, so I'm checking with {who}. I'll come back to it in a minute; anything else meanwhile?",
                 f"Let me check that with {who} rather than guess. I'll circle back shortly."]
    return lines[turn % len(lines)]


def relay_line(name: str, answer: str, company: bool = False) -> str:
    text = answer.strip()
    if text and text[-1] not in ".!?":
        text += "."
    who = "The team" if company else name
    return f"Coming back to your earlier question — {who} says: {text}"


def apology_line(name: str, topic: str, question: str, company: bool = False) -> str:
    """No reply in time. Owns it, does not invent, promises a real follow-up
    — shaped by what was asked, because "sorry, I don't know" lands very
    differently on a pricing question than on an API one."""
    who = _whom(name, company)
    if topic == "pricing":
        return (f"On the pricing question — I couldn't reach {who} just now, and I'd rather not quote "
                f"you something wrong. You'll get it in writing from {who} today.")
    if topic == "timeline":
        return (f"About that timeline — {who} is tied up, and I won't promise a date I can't back up. "
                f"{who[0].upper() + who[1:]} will confirm it by email after this call.")
    if topic == "security":
        return (f"Sorry — on the security question I couldn't get {who} in time, and it's one I want "
                f"exactly right. We'll send you the details in writing after the call.")
    if topic == "commitment":
        return (f"I can't commit to that on {who}'s behalf without checking, and I couldn't reach them just now — "
                f"apologies. It's on {'our' if company else 'their'} list and you'll hear back after the call.")
    return (f"Sorry, I couldn't get hold of {who} on that one in time. I've noted it, and "
            f"{'we' if company else who} will follow up with the exact answer after the call.")
