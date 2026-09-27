from __future__ import annotations
import re
from app.models import QueryIntent


# Rule-based patterns for intent classification (fast, no LLM call needed)
_STRUCTURAL_PATTERNS = [
    r"\bwhere\b.*\b(is|are|live|lives|located|defined|implemented)\b",
    r"\bwhich (file|module|class|function)\b",
    r"\bfind\b.*\b(class|function|method|module)\b",
    r"\bwhere\b.*\b(auth|router|handler|model|service|controller)\b",
    r"\bshow me\b.*\bcode\b",
]

_RELATIONAL_PATTERNS = [
    r"\bwhat (calls|imports|uses|depends on|references)\b",
    r"\bwho calls\b",
    r"\bcall(ers?|ees?|graph|chain)\b",
    r"\bdependenc(y|ies)\b",
    r"\bimport(s|ed by)?\b.*\bfrom\b",
    r"\brelationship\b",
]

_CROSS_CUTTING_PATTERNS = [
    r"\bhow does\b.*\bflow\b",
    r"\bend.to.end\b",
    r"\blifecycle\b",
    r"\bpipeline\b",
    r"\btrace\b.*\brequest\b",
    r"\bwalk me through\b",
    r"\bsequence\b",
]

_SEMANTIC_PATTERNS = [
    r"\bwhat does\b",
    r"\bhow does\b.*\bwork\b",
    r"\bexplain\b",
    r"\bdescribe\b",
    r"\bpurpose of\b",
    r"\bsummariz\b",
]


def _match_any(text: str, patterns: list[str]) -> bool:
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in patterns)


async def classify_intent(question: str) -> QueryIntent:
    """
    Classify human NL question into one of four intent categories.
    Uses rule-based patterns first; most questions are unambiguous.
    """
    if _match_any(question, _RELATIONAL_PATTERNS):
        return QueryIntent.RELATIONAL
    if _match_any(question, _CROSS_CUTTING_PATTERNS):
        return QueryIntent.CROSS_CUTTING
    if _match_any(question, _STRUCTURAL_PATTERNS):
        return QueryIntent.STRUCTURAL
    if _match_any(question, _SEMANTIC_PATTERNS):
        return QueryIntent.SEMANTIC

    # Default: semantic (most general)
    return QueryIntent.SEMANTIC
