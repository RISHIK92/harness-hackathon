"""Whether a suggestion is safe to read out to the client.

A whisper suggestion is different from a spoken answer in one way that
matters: the member is about to SAY IT to a customer, in their own voice,
and once said it cannot be taken back. So each suggestion carries what the
member needs to decide with — not a score, a reason.

Every warning here is derived from the answer contract itself (source types,
confidence, abstention, whether the verifier stripped anything). Nothing is
inferred from the prose, because a warning that is itself a guess is worse
than no warning: it trains people to ignore the label.
"""
from __future__ import annotations

# Sources a customer is not entitled to hear quoted. Slack is the clearest
# case — the Bangalore pricing rationale lives in a thread where somebody
# named the partner and the commission, which is true, cited, and absolutely
# not for the customer's ear.
INTERNAL_SOURCE_TYPES = {"slack", "ticket", "call", "incident", "log", "commit", "pr"}

# Sources that are either the product's own documentation or the code itself
# — safe to paraphrase to a customer.
CLIENT_SAFE_SOURCE_TYPES = {"docs", "code", "account", "screen"}


def warnings_for(answer: dict) -> list[dict]:
    """`[{code, label, detail}]`, most serious first.

    Returns a list rather than one flag because these genuinely stack: a
    low-confidence answer built only on Slack is two different problems, and
    collapsing them would hide one.
    """
    found: list[dict] = []

    evidence = [e for call in answer.get("tool_trace") or [] for e in (call.get("evidence") or [])]
    source_types = {e.get("source_type") for e in evidence}

    if answer.get("abstained"):
        found.append({
            "code": "no_answer",
            "label": "Don't say this",
            "detail": "Photon couldn't ground an answer — this is what it doesn't have, not what's true.",
        })

    if source_types and source_types <= INTERNAL_SOURCE_TYPES:
        found.append({
            "code": "internal_only",
            "label": "Internal source only",
            "detail": "Every source here is internal (" + ", ".join(sorted(source_types))
                      + "). Reword before saying it — don't quote it.",
        })
    elif source_types & INTERNAL_SOURCE_TYPES:
        found.append({
            "code": "mixed_sources",
            "label": "Contains internal detail",
            "detail": "Some of this comes from " + ", ".join(sorted(source_types & INTERNAL_SOURCE_TYPES))
                      + " — safe in substance, not in wording.",
        })

    if answer.get("confidence") == "low" and not answer.get("abstained"):
        found.append({
            "code": "low_confidence",
            "label": "Check before saying",
            "detail": "Weak or partly unverified evidence — the claim may not hold.",
        })

    if not evidence and not answer.get("abstained"):
        # Should be impossible (the loop abstains with no evidence) but a
        # suggestion with nothing behind it is exactly the thing that must
        # never reach a customer unlabelled.
        found.append({
            "code": "uncited",
            "label": "Uncited",
            "detail": "No evidence came back with this. Treat it as unverified.",
        })

    if answer.get("escalation"):
        found.append({
            "code": "escalate",
            "label": f"Suggests escalating to {answer['escalation']}",
            "detail": "Photon thinks this belongs with someone else.",
        })

    return found


def is_safe_to_read_aloud(warnings: list[dict]) -> bool:
    """True only when nothing would mislead the customer if read verbatim."""
    blocking = {"no_answer", "internal_only", "uncited", "low_confidence"}
    return not any(w["code"] in blocking for w in warnings)
