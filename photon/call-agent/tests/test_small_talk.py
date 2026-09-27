"""The classifier's whole job is to be conservative: never swallow a real
question. These cases are real utterances from live testing plus the
voice-shaped cases from server/evals/agent_eval.py's HARD set.

    cd call-agent && .venv/bin/python -m pytest tests -q
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from small_talk import Turn, classify

GREETINGS = [
    "Hello. How are you?",          # the exact utterance that cost 4.5s live
    "hi", "Hey Photon", "hello",
    "Good morning", "hey how's it going", "hi, how are you doing?",
]

IGNORE = [
    "One sec, the phone.",          # also from live testing
    "um", "okay", "yeah", "thanks", "got it", "sounds good",
    "hold on", "just a second", "sorry", "never mind", "bye",
    "yeah sorry",
    "yeah sorry one second",
    "let me grab my coffee",
    "ok cool thanks",
]

ANSWER = [
    # real questions, tidy and ASR-mangled
    "why does pricing have a special case for Bangalore?",
    "why is bangalore pricing different from other cities",
    "uh so the webhooks for north wind are failing can you check",
    "our Mumbai customer says their integration broke last week, why?",
    "how many times does Meridian retry a failed webhook?",
    "which accounts do you know about",
    "check my screen and help me open the search bar?",
    # statements with no interrogative at all, but clearly product work
    "northwind's webhooks are broken",
    "the docs say five retries",
    "Calico is on the partner tier",
    # a greeting that carries a real question along with it
    "hi Photon, why is Calico billed differently?",
    "hello, can you check Northwind for me",
]


@pytest.mark.parametrize("text", GREETINGS)
def test_greetings_answered_locally(text):
    assert classify(text) is Turn.GREETING


@pytest.mark.parametrize("text", IGNORE)
def test_ambient_speech_ignored(text):
    assert classify(text) is Turn.IGNORE


@pytest.mark.parametrize("text", ANSWER)
def test_real_requests_always_reach_the_pipeline(text):
    assert classify(text) is Turn.ANSWER


def test_empty_is_ignored():
    assert classify("   ") is Turn.IGNORE


def test_long_utterance_is_never_swallowed():
    # Length alone protects against a filler prefix eating a real question.
    assert classify("okay so the thing is our integration keeps returning errors") is Turn.ANSWER


# ── screen-share path ────────────────────────────────────────────────────
# The gate runs BEFORE any visual handling, so a question about the screen
# that gets classified as chatter would silently disable screen share.

SCREEN_UTTERANCES = [
    "check my screen and help me open the search bar?",
    "can you see my screen",
    "look at my screen",
    "what's on my screen right now",
    "where do I find the signing secret",
    "what am I looking at",
    "does this look right to you",
    "am I in the right place",
]


@pytest.mark.parametrize("text", SCREEN_UTTERANCES)
def test_screen_questions_reach_the_pipeline(text):
    assert classify(text) is Turn.ANSWER


# The old VISUAL_HINT_RE keyword gate is gone (see _wants_visual_context):
# an active share is the signal now, so what matters here is only that a
# screen question is never swallowed as small talk before it can reach the
# frame-attach path. That makes classify() the LAST gate in front of vision,
# which is why these cases live on.


# ── Indic greetings get the same instant path as English ─────────────────

INDIC_GREETINGS = ["నమస్కారం", "నమస్తే", "హలో", "வணக்கம்", "ஹலோ", "नमस्ते", "नमस्कार", "हैलो"]


@pytest.mark.parametrize("text", INDIC_GREETINGS)
def test_indic_greetings_are_answered_locally(text):
    assert classify(text) is Turn.GREETING


INDIC_QUESTIONS = [
    "బెంగళూరు ధరలు ఎందుకు ప్రత్యేకంగా ఉన్నాయి?",
    "பெங்களூரு விலை ஏன் வித்தியாசமாக உள்ளது?",
    "बैंगलोर की कीमत अलग क्यों है?",
    "నమస్కారం, బెంగళూరు ధరల గురించి చెప్పండి?",   # greeting + a real question
]


@pytest.mark.parametrize("text", INDIC_QUESTIONS)
def test_indic_questions_still_reach_the_pipeline(text):
    assert classify(text) is Turn.ANSWER
