"""Conversation memory within one call: what was just asked and answered.

Until this existed, `answer_question()` received a bare question string and
nothing else, so a follow-up had no referent at all. "Why is that?" reached
the planner as three words with no subject, and the fast path handed those
same three words to `search_code` as a semantic query. The transcript the
call-agent kept (`TurnState.transcript`) was written and never read.

Two separate jobs, deliberately kept apart because they fail differently:

  RETRIEVAL  `resolve_query()` — what string the search tools get. This must
             work with no LLM and no latency, because the whole point of the
             fast path (loop._fast_path_calls) is that it skips the planner's
             ~1.7s round-trip. An LLM rewrite here would hand that saving
             straight back.

  PROMPTING  `format_history_block()` — what the planner and the compose step
             READ. Here the model can resolve the reference itself, which it
             does better than any regex, so the regex is not asked to.

The detection below is therefore allowed to be approximate in one direction
only. A false positive prepends the previous question to a search string —
slightly noisier retrieval. A false negative is a follow-up searched as if it
were a standalone question, which is the failure this module exists to fix.
So the bias is toward treating a short, referring utterance as a follow-up.
"""
from __future__ import annotations

import re

# Citation markers are stripped out of remembered agent text. An [ev_xxx] id
# is only valid for the turn that produced it: leave one in the history block
# and the compose model will happily reuse it this turn, where it names
# nothing. The verifier would then strip that claim as uncited — a correct
# answer thrown away by its own memory.
_MARKER_RE = re.compile(r"\s*\[(?:ev_[0-9a-f]+)(?:\s*,\s*ev_[0-9a-f]+)*\]")

MAX_HISTORY_TURNS = 6
MAX_TURN_CHARS = 400
# Longer than this and the utterance carries its own subject, whatever
# pronouns it also happens to contain.
_MAX_FOLLOW_UP_WORDS = 12

# A pronoun with no antecedent in its own sentence. "Why is that slower?",
# "who decided it", "are they still failing".
_ANAPHORA_RE = re.compile(
    r"\b(that|this|those|these|it|its|it's|they|them|their|there|the same|same one|"
    r"he|him|his|she|her|hers)\b",
    re.I,
)

# Openers that only make sense as a continuation. Deliberately does NOT
# include a bare "why" / "who" / "when": "why does pricing have a special
# case for Bangalore" is a complete question that opens with why, and
# prepending the previous one to it would only blur the search.
_ELLIPSIS_RE = re.compile(
    r"^\s*(?:"
    r"(?:and|but|so|also|ok(?:ay)?)\b"
    r"|(?:what|how)\s+about\b"
    r"|(?:tell me more|more on|more about|elaborate|go on|say more|carry on)\b"
    r"|(?:what else|anything else|why not|how come|since when|says who|really)\b"
    r")",
    re.I,
)

# A very short interrogative IS a follow-up — there is nothing else in it.
_BARE_INTERROGATIVE_RE = re.compile(r"^\s*(why|who|whose|when|where|how|which|what)\b", re.I)
# The same, for the languages the voice stack speaks. Script detection
# (call-agent/language.py) gives us the language; these give us the shape.
_INDIC_FOLLOW_UP_RE = re.compile(r"కారణం|ఎందుకు|ஏன்|क्यों|क्या|ఇది|அது|वो|उसका")


def strip_markers(text: str) -> str:
    """Drop [ev_xxx] citation markers — see _MARKER_RE's note above."""
    return _MARKER_RE.sub("", text or "").strip()


def normalise_history(raw) -> list[dict]:
    """Sanitise caller-supplied history into [{role, text}].

    The caller is the call-agent worker or the browser, so this is
    client-asserted like everything else on the /ask route (see
    routers/agent.py). It is only ever read as prose, never as instructions
    about which tools to call or which tenant to read — the tool list and
    workspace_id are resolved server-side and cannot be moved from here.
    """
    if not raw:
        return []
    turns: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            role, text = item.get("role"), item.get("text")
        else:  # pydantic model
            role, text = getattr(item, "role", None), getattr(item, "text", None)
        role = "agent" if str(role or "").lower() in ("agent", "assistant") else "user"
        text = strip_markers(str(text or ""))
        if not text:
            continue
        turns.append({"role": role, "text": text[:MAX_TURN_CHARS]})
    return turns[-MAX_HISTORY_TURNS:]


def last_user_question(history: list[dict]) -> str | None:
    for turn in reversed(history):
        if turn["role"] == "user":
            return turn["text"]
    return None


def is_follow_up(question: str, history: list[dict]) -> bool:
    """Whether this utterance only makes sense against what came before."""
    q = (question or "").strip()
    if not q or not history:
        return False
    words = len(q.split())
    if _ELLIPSIS_RE.match(q):
        return True
    if words <= _MAX_FOLLOW_UP_WORDS and _ANAPHORA_RE.search(q):
        return True
    if words <= 5 and (_BARE_INTERROGATIVE_RE.match(q) or _INDIC_FOLLOW_UP_RE.search(q)):
        return True
    return False


def resolve_query(question: str, history: list[dict]) -> tuple[str, bool]:
    """The string the SEARCH TOOLS should use, and whether it was rewritten.

    Concatenation rather than an LLM rewrite, on purpose. The tools this
    feeds are semantic search: a query of "why does pricing have a special
    case for Bangalore why is that" embeds almost exactly where the properly
    rewritten sentence does, and costs 0ms instead of a second round-trip.
    A grammatical rewrite would read better and retrieve the same.
    """
    q = (question or "").strip()
    if not is_follow_up(q, history):
        return q, False
    previous = last_user_question(history)
    if not previous:
        return q, False
    return f"{previous} {q}"[: MAX_TURN_CHARS * 2], True


def format_history_block(history: list[dict], *, speaker: str = "Caller", agent: str = "You") -> str:
    """The prompt block. Empty string when there is no history, so a first
    turn's prompt is byte-for-byte what it was before this module existed."""
    if not history:
        return ""
    lines = [f"{speaker if t['role'] == 'user' else agent}: {t['text']}" for t in history]
    return "Earlier in this conversation (oldest first):\n" + "\n".join(lines) + "\n"
