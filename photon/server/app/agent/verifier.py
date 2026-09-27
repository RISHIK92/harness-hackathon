"""Enforces the three non-negotiable rules from CLAUDE.md Section 4 after
composition: no uncited claim, abstain over guess. Runs purely on the
composed dict + the set of evidence ids that actually came back from tool
calls this turn — no LLM call, no transport, fully deterministic.
"""
from __future__ import annotations

import re

import structlog

log = structlog.get_logger()

# Matches an id anywhere, NOT only when it is alone in its own brackets.
# The compose model routinely emits "[ev_80abd768, ev_33f954d5]", and the
# old `\[(ev_[0-9a-f]+)\]` pattern found nothing at all in that — so on the
# no-structured-claims path a perfectly well-cited answer was treated as
# having zero valid markers and thrown away as an abstention. Caught while
# testing Hindi answers, but nothing about it is language-specific.
_MARKER_RE = re.compile(r"ev_[0-9a-f]+")


def verify(composed: dict, valid_evidence_ids: set[str]) -> dict:
    answer = composed.get("answer", "") or ""
    claims = composed.get("claims") or []
    abstained = bool(composed.get("abstained", False))
    escalation = composed.get("escalation")

    if abstained or not valid_evidence_ids:
        return {
            "answer": answer or "I don't have evidence for that.",
            "claims": [],
            "confidence": "low",
            "abstained": True,
            "escalation": escalation,
        }

    if not claims:
        # No structured claims to verify sentence-by-sentence — fall back to
        # checking that every [ev_xxx] marker actually present in the answer
        # text is one we really have evidence for.
        markers = set(_MARKER_RE.findall(answer))
        if not markers or not markers.issubset(valid_evidence_ids):
            log.warning("agent.verifier_no_valid_markers", answer=answer[:200])
            return {
                "answer": "I don't have verifiable evidence for that.",
                "claims": [],
                "confidence": "low",
                "abstained": True,
                "escalation": escalation,
            }
        return {"answer": answer, "claims": [], "confidence": "medium", "abstained": False, "escalation": None}

    valid_claims, invalid_claims = [], []
    for c in claims:
        ids = c.get("evidence_ids") or []
        if ids and all(i in valid_evidence_ids for i in ids):
            valid_claims.append(c)
        else:
            invalid_claims.append(c)

    fail_ratio = len(invalid_claims) / len(claims)

    if fail_ratio > 0.5:
        log.warning("agent.verifier_majority_uncited", fail_ratio=fail_ratio, claims=len(claims))
        return {
            "answer": "I don't have solid enough evidence to answer that confidently.",
            "claims": [],
            "confidence": "low",
            "abstained": True,
            "escalation": escalation,
        }

    cleaned_answer = answer
    for c in invalid_claims:
        text = c.get("text", "")
        if text and text in cleaned_answer:
            cleaned_answer = cleaned_answer.replace(text, "").strip()
        else:
            log.warning("agent.verifier_could_not_strip_claim", text=text[:120])
    cleaned_answer = re.sub(r"\s{2,}", " ", cleaned_answer).strip() or answer

    confidence = "high" if not invalid_claims else "low"

    return {
        "answer": cleaned_answer,
        "claims": valid_claims,
        "confidence": confidence,
        "abstained": False,
        "escalation": None,
    }
