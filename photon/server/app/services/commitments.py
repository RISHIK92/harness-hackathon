"""Commitments made on a call — "we'll fix that by Friday" — turned into work.

Only OUR side's lines count: a client saying "you'll fix it, right?" is a
request, and the agent drafting a ticket off a customer's assumption would
be putting words in our mouth. Only FIX-shaped commitments become agent-job
drafts; "I'll send you the deck" is a promise too, but not one the harness
can keep.

A draft is never planned on its own. The member confirms it (and picks the
repo) first — a sentence said in passing on a call is not an approved ticket.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_WHO = r"(?:we(?:'ll| will| can| are going to|'re going to| should)|i(?:'ll| will| can| am going to|'m going to)|let me|let us|let's)"
_FIX = r"(?:fix|patch|resolve|sort out|look into|investigate|debug|correct|repair|get .{0,20}? working|ship a fix)"
COMMITMENT = re.compile(rf"\b{_WHO}\b[^.?!]{{0,40}}?\b{_FIX}\b[^.?!]*", re.I)
DUE = re.compile(
    r"\b(?:by|before|until|on)\s+(?:the\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"tomorrow|tonight|end of (?:the )?(?:day|week|month|sprint)|eod|eow|next (?:week|sprint|release)|"
    r"\d{1,2}(?:st|nd|rd|th)?(?: of)? ?\w*)\b", re.I)
# Hedges that make it not a commitment at all.
HEDGE = re.compile(r"\b(maybe|might|if we can|not sure|i don't think|we can't|won't|cannot)\b", re.I)


@dataclass
class Commitment:
    sentence: str
    due: Optional[str]


def detect(text: str) -> Optional[Commitment]:
    for sentence in re.split(r"(?<=[.?!])\s+", (text or "").strip()):
        m = COMMITMENT.search(sentence)
        if not m or HEDGE.search(sentence) or sentence.rstrip().endswith("?"):
            continue
        due = DUE.search(sentence)
        return Commitment(sentence=sentence.strip(), due=due.group(1).lower() if due else None)
    return None


def title_for(commitment: Commitment, problem_lines: list[str]) -> str:
    """Short, and about the PROBLEM when we have it — "Export times out on
    large accounts" is a ticket title, "we'll fix that by Friday" is not."""
    source = next((l for l in reversed(problem_lines) if len(l.split()) >= 4), None) or commitment.sentence
    title = re.sub(r"\s+", " ", source).strip().rstrip(".?!")
    return (title[:77] + "…") if len(title) > 80 else title


def issue_text_for(commitment: Commitment, problem_lines: list[str],
                   meeting_title: Optional[str], speaker: str) -> str:
    parts = [f"Raised on a call{f' ({meeting_title})' if meeting_title else ''}."]
    if problem_lines:
        parts += ["", "What the client described:"] + [f"> {l}" for l in problem_lines[-4:]]
    parts += ["", f"{speaker} committed: \"{commitment.sentence}\""]
    if commitment.due:
        parts.append(f"Promised by: {commitment.due}")
    return "\n".join(parts)
