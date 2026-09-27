"""Deterministic, zero-latency triage for open-mic speech.

With the wake word gone (explicit user request), EVERY finalized utterance
reaches the agent — including "Hello. How are you?" and "one sec, the
phone." Sending those through the full pipeline cost a real measured 4.5s
(2.4s planning + a 660ms search_docs + 1.4s compose) to answer a greeting,
and worse, it means the agent talks over people who weren't addressing it.

So: classify locally first. No LLM call, no network, no tools — a regex
pass costs microseconds and cannot fail closed.

The bias is deliberately asymmetric. Misclassifying a real question as
chatter is BAD (the agent silently ignores a customer), so every rule here
is conservative: short utterances only, and anything carrying a question
mark, an interrogative, or a product/account word always falls through to
the full pipeline no matter what else it matches.
"""
from __future__ import annotations

import re
from enum import Enum


class Turn(str, Enum):
    GREETING = "greeting"      # answer instantly with a canned line
    IGNORE = "ignore"          # not addressed to us — say nothing at all
    ANSWER = "answer"          # the real pipeline


_MAX_SMALL_TALK_WORDS = 8

# Any of these and it goes to the pipeline, whatever else it looks like.
_QUESTION_SIGNAL = re.compile(
    r"\?|\b(why|what|what'?s|how|when|where|who|which|whose|can|could|would|should|does|do|did|is|are|was|were"
    r"|tell me|show me|explain|check|look up|find|help me|any (idea|update|news))\b",
    re.IGNORECASE,
)

# Domain words mean it's about the product even if phrased casually
# ("northwind's webhooks are broken" has no interrogative at all).
_DOMAIN_SIGNAL = re.compile(
    r"\b(meridian|webhook|webhooks|pricing|price|rate|rates|account|accounts|booking|bookings|incident"
    r"|ticket|retry|retries|secret|api|integration|error|failing|failed|broken|down|401|northwind|calico|orion"
    r"|bangalore|partner|invoice|billing|slack|commit|deploy|docs|documentation)\b",
    re.IGNORECASE,
)

# Indic greetings, so a Telugu/Tamil/Hindi "hello" gets the same instant
# 0ms reply an English one does instead of paying for a full pipeline turn.
# Script-specific, so there is no chance of colliding with English words.
_GREETING_INDIC = re.compile(
    r"^\s*(నమస్కారం|నమస్తే|హలో|హాయ్|ఏమండీ"        # Telugu
    r"|வணக்கம்|ஹலோ|ஹாய்"                              # Tamil
    r"|नमस्ते|नमस्कार|हैलो|हाय|प्रणाम)"                  # Hindi
    r"[\s,.!?-]*(ఎలా ఉన్నారు|எப்படி இருக்கிறீர்கள்|कैसे हैं|कैसे हो)?[\s,.!?-]*$",
)

_GREETING = re.compile(
    r"^(hi|hey|hello|heya|yo|good (morning|afternoon|evening)|greetings)\b"
    r"[\s,.!-]*(photon\b)?[\s,.!-]*"
    r"(how are (you|things|we) ?(doing|going)?|how'?s it going|how do you do|what'?s up|you there|are you there)?"
    r"[\s,.!?-]*$",
    re.IGNORECASE,
)

# Standalone acknowledgements, fillers, and "talking to someone else".
# Matched as CLAUSES, not as one whole-string alternation: real ambient
# speech is chained ("yeah sorry", "One sec, the phone."), and a single
# anchored pattern only ever caught the one-word case.
_FILLER_CLAUSE = re.compile(
    r"^(um+|uh+|erm+|hm+|mm+|ah+|oh+|ok|okay|alright|right|sure|well|so"
    r"|yeah|yep|yup|yes|no|nope|nah|thanks|thank you|cheers|cool|nice|great|perfect"
    r"|got it|understood|sounds good|makes sense|of course|no worries|all good"
    r"|one (sec|second|moment|minute)|just a (sec|second|moment)|hold on|hang on|wait"
    r"|give me a (sec|second|minute)|sorry|excuse me|my bad|never ?mind|bye|goodbye"
    r"|see you|talk (to you )?later|brb|be right back|the phone|it'?s the phone"
    r"|let me (get|grab|check|take) [a-z' ]+|i'?ll be right back|back in a (sec|second|minute))"
    r"\b",
    re.IGNORECASE,
)
_SEPARATOR = re.compile(r"^[\s,.!;:—–-]+")


def _is_all_filler(text: str) -> bool:
    """True only if the utterance is filler clauses end to end. Anything
    left over that isn't filler means it might be real speech to us."""
    rest = text.strip()
    consumed = False
    while rest:
        rest = _SEPARATOR.sub("", rest)
        if not rest:
            break
        match = _FILLER_CLAUSE.match(rest)
        if not match:
            return False
        rest = rest[match.end():]
        consumed = True
    return consumed


GREETING_REPLY = "Hi — I'm here and listening. Ask me anything whenever you're ready."


def classify(text: str) -> Turn:
    stripped = (text or "").strip()
    if not stripped:
        return Turn.IGNORE

    words = stripped.split()

    # A greeting is checked BEFORE the question guard on purpose: "how are
    # you" is literally interrogative but is not a question for the agent.
    if len(words) <= _MAX_SMALL_TALK_WORDS and (
        _GREETING.match(stripped) or _GREETING_INDIC.match(stripped)
    ):
        return Turn.GREETING

    # From here on, anything that looks like a real request wins.
    if _QUESTION_SIGNAL.search(stripped) or _DOMAIN_SIGNAL.search(stripped):
        return Turn.ANSWER

    if len(words) <= _MAX_SMALL_TALK_WORDS and _is_all_filler(stripped):
        return Turn.IGNORE

    return Turn.ANSWER
