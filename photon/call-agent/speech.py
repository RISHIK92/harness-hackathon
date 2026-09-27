"""Turning a composed answer into something safe to say out loud.

This is the "Reference Pronunciations" block every voice-agent prompting
guide asks for (OpenAI's realtime guide names it as one of its eight
sections), implemented as CODE rather than as a prompt instruction — and the
difference is forced, not stylistic. Those guides target speech-to-speech
models, where the thing that writes the words is the thing that says them.
Photon composes text ONCE and then uses it twice: the browser renders it with
citation chips, and TTS reads it aloud. A prompt rule like "say four oh one
instead of 401" would therefore change the displayed answer too, break
`claims[].text` as a verbatim substring of it, and break an eval that asserts
on the literal string "401". Shaping the spoken copy here cannot touch any of
that, costs no tokens, and is deterministic.

The answer carries inline `[ev_7a3f]` citation markers — that's the whole
grounding contract and the evidence panel renders them as chips. But TTS
reads them literally, so on a live call the agent actually said:

    "Meridian is a B2B booking and scheduling platform ev 20021cda."

Strip them for speech ONLY. The structured answer that goes to the browser
keeps every marker, so nothing about "no uncited claim" changes — this is
purely the difference between what's shown and what's spoken, which the
voice rules already assume ("never read a file path or line number aloud;
it's shown on screen instead").
"""
from __future__ import annotations

import re

# Handles a lone marker, several ids inside one bracket ("[ev_a, ev_b]" —
# the compose model really does emit these), and runs of adjacent markers.
# The leading \s* eats the space before the bracket so "platform [ev_x]."
# closes up to "platform." instead of leaving "platform ."
_CITATION = re.compile(r"\s*\[\s*ev_[0-9a-f]+(?:\s*,\s*ev_[0-9a-f]+)*\s*\]", re.IGNORECASE)
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?])")
_MULTISPACE = re.compile(r"[ \t]{2,}")

# Markdown the compose model emits occasionally. A TTS engine either reads the
# characters out ("star star", "backtick") or runs the words together.
_BACKTICKS = re.compile(r"`+")
_EMPHASIS = re.compile(r"(\*{1,3}|_{2,})(?=\S)(.+?)(?<=\S)\1", re.DOTALL)

# A path-ish token: at least one slash, ending in a known source extension,
# optionally with a :L12-L34 line range (the locator shape this repo uses).
# Said aloud in full it becomes "s r c slash controllers slash drill
# controller dot t s", which is unusable — a person says "the drill
# controller". The voice rules already forbid reading a path aloud; this is
# what to do when one gets through anyway.
_PATH = re.compile(r"\b[\w.-]+(?:/[\w.-]+)+\.[A-Za-z]{1,4}\b(?::L\d+(?:-L\d+)?)?")

# Identifiers. Kept separate because they need opposite treatment: an
# underscore is a word break, an internal capital is a word break, and an
# acronym run (HTTPError, JWTAuth) must not be split letter by letter.
_SNAKE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b")
_CAMEL = re.compile(r"\b[a-z][a-z0-9]*(?:[A-Z][a-z0-9]+)+\b|\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b")
_ACRONYM_THEN_WORD = re.compile(r"\b([A-Z]{2,})([A-Z][a-z]+)")


def _split_camel(token: str) -> str:
    token = _ACRONYM_THEN_WORD.sub(r"\1 \2", token)
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", token)


def _say_path(match: re.Match) -> str:
    """The last component, without its extension or line range."""
    name = match.group(0).split(":")[0].rstrip("/").split("/")[-1]
    return _split_camel(name.rsplit(".", 1)[0])


def for_speech(answer: str) -> str:
    """The answer with citation markers removed, spacing repaired, and
    written-only shapes turned into something a TTS can actually say.

    Deliberately conservative: everything here is a shape that is unreadable
    aloud in EVERY engine, not a guess about one vendor's voice. Numbers are
    left exactly as written — "401" and "3 retries" are read correctly, and
    rewriting them to words would only cost accuracy if this ever ran on the
    displayed copy by mistake.
    """
    if not answer:
        return ""
    spoken = _CITATION.sub("", answer)
    spoken = _BACKTICKS.sub("", spoken)
    spoken = _EMPHASIS.sub(r"\2", spoken)
    spoken = _PATH.sub(_say_path, spoken)
    spoken = _SNAKE.sub(lambda m: _split_camel(m.group(0).replace("_", " ")), spoken)
    # Applied globally, not only inside a camel match: "HTTPError" is an
    # acronym glued to a word and matches no camelCase pattern (there is no
    # lowercase letter after the first capital), so it would otherwise survive
    # as one unsayable token.
    spoken = _ACRONYM_THEN_WORD.sub(r"\1 \2", spoken)
    spoken = _CAMEL.sub(lambda m: _split_camel(m.group(0)), spoken)
    spoken = _SPACE_BEFORE_PUNCT.sub(r"\1", spoken)
    spoken = _MULTISPACE.sub(" ", spoken)
    return spoken.strip()


# ── Thinking sounds ──────────────────────────────────────────────────────
#
# Added at the SPOKEN layer, never in the prompt, and the distinction is the
# whole reason this is safe. Licensing the model to open with "Ah" / "Yeah"
# regressed accuracy 5/5 (see app/agent/prompts.py's note above _RULES),
# because a conversational opener gets chosen BEFORE the evidence is weighed
# and the rest of the sentence is then written to justify it. Here the answer
# is already composed, verified and cited — the sound is glued on afterwards
# and cannot reach back and change what was decided.
#
# It also never touches the published answer or the transcript: only the
# string handed to adapter.speak().
#
# The empty slots are deliberate. A thinking sound on EVERY turn is its own
# tic, and the whole point is to sound less mechanical, so the rotation
# yields silence about a third of the time.
THINKING_SOUNDS = {
    "en-IN": ["Hmm,", "", "Ah,", "", "Mmm,", "Right,", "", "So,"],
    # Not "hmm" transliterated: Indic TTS handles a real word far more
    # reliably than an onomatopoeic one, and a mangled filler is worse than
    # none. These are the natural spoken particles in each language.
    "hi-IN": ["हम्म,", "", "अच्छा,", "", "हाँ,", "तो,", "", "ठीक है,"],
    "te-IN": ["అలాగే,", "", "సరే,", "", "అవును,", "అయితే,", "", "మరి,"],
    "ta-IN": ["ம்ம்,", "", "சரி,", "", "ஆமா,", "ஓ,", "", "அப்போ,"],
}


def with_thinking_sound(text: str, language: str | None = None, index: int = 0) -> str:
    """Prefix a short human sound, rotating (and sometimes nothing at all).

    `index` is the turn counter rather than randomness, for the same reason
    the acknowledgements rotate that way: back-to-back turns are guaranteed to
    differ, which random choice would not be, and tests stay reproducible.
    """
    spoken = (text or "").strip()
    if not spoken:
        return spoken
    sounds = THINKING_SOUNDS.get(language or "en-IN") or THINKING_SOUNDS["en-IN"]
    sound = sounds[index % len(sounds)]
    if not sound:
        return spoken
    # Don't stack one onto an answer that already opens with a particle.
    first = spoken.split(maxsplit=1)[0].rstrip(",").lower()
    if first in {"hmm", "ah", "mmm", "right", "so", "yeah", "yep", "no", "okay", "ok", "well"}:
        return spoken
    return f"{sound} {spoken[0].lower() + spoken[1:] if spoken[:2].isupper() is False else spoken}"
