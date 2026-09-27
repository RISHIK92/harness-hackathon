import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from speech import for_speech

CASES = [
    # the exact line the agent spoke on the live call
    ("Hello! Meridian is a B2B booking and scheduling platform [ev_20021cda]. How can I help you today?",
     "Hello! Meridian is a B2B booking and scheduling platform. How can I help you today?"),
    # multiple ids in one bracket - the compose model really emits these
    ("Bangalore gets preferential rates [ev_80abd768, ev_4879aa12].",
     "Bangalore gets preferential rates."),
    # several markers across sentences
    ("Your endpoint returns 401 [ev_7a3f]. The secret rotated on Aug 14 [ev_2b91].",
     "Your endpoint returns 401. The secret rotated on Aug 14."),
    # adjacent markers
    ("Partner tier applies here [ev_aaa111][ev_bbb222].", "Partner tier applies here."),
    # mid-sentence marker keeps the sentence readable
    ("The rate is 0.88 [ev_abc123] for partner accounts.",
     "The rate is 0.88 for partner accounts."),
    # nothing to strip
    ("I don't have evidence for that.", "I don't have evidence for that."),
    ("", ""),
]


@pytest.mark.parametrize("raw,expected", CASES)
def test_markers_are_stripped_for_speech(raw, expected):
    assert for_speech(raw) == expected


def test_no_marker_syntax_survives():
    import re
    out = for_speech("A [ev_1a2b] B [ev_3c4d, ev_5e6f] C [ev_beef01]")
    assert "ev_" not in out and "[" not in out and "]" not in out


def test_unrelated_brackets_are_left_alone():
    # Only citation markers go; other bracketed text is the answer's own. The
    # underscore inside it is separately spoken as a space (see the
    # pronunciation tests below) — that is the identifier being made sayable,
    # not the bracket being touched.
    assert for_speech("The field [webhook_url] is empty [ev_1a2b].") == "The field [webhook url] is empty."
    assert for_speech("Check [this] out [ev_1a2b].") == "Check [this] out."


# ── Reference pronunciations: written shapes a TTS cannot say ─────────────
# These live in code rather than in the prompt because the same composed text
# is ALSO rendered in the browser and asserted on by server/evals — see the
# module docstring in speech.py.


def test_screaming_snake_constants_lose_their_underscores():
    assert for_speech("It comes from PARTNER_CITY_RATES.") == "It comes from PARTNER CITY RATES."


def test_snake_case_fields_lose_their_underscores():
    assert for_speech("The field webhook_url is empty.") == "The field webhook url is empty."


def test_camel_case_identifiers_are_split_into_words():
    assert for_speech("It calls getUserById first.") == "It calls get User By Id first."


def test_an_acronym_glued_to_a_word_is_split():
    """HTTPError matches no camelCase pattern — there's no lowercase letter
    after the first capital — so it needs its own pass or it survives whole."""
    assert for_speech("It raises HTTPError.") == "It raises HTTP Error."


def test_a_file_path_becomes_the_thing_it_names():
    """Read in full this is "s r c slash controllers slash drill controller
    dot t s". The voice rules already forbid saying a path aloud; this is the
    fallback for when one gets through."""
    assert for_speech("See src/controllers/drillController.ts for that.") == "See drill Controller for that."


def test_a_locator_line_range_is_not_read_out():
    assert for_speech("In app/pricing.py:L42-L58 there.") == "In pricing there."


def test_markdown_is_not_read_aloud():
    assert for_speech("The field `webhook_url` is **empty**.") == "The field webhook url is empty."


def test_numbers_are_left_exactly_as_written():
    """Deliberate: every TTS says these correctly, and rewriting them would
    only create a way for the spoken and displayed answers to disagree."""
    assert for_speech("Returns 401 after 3 retries.") == "Returns 401 after 3 retries."


def test_ordinary_prose_is_untouched():
    text = "Bangalore has a reseller agreement, so partner accounts get a lower rate."
    assert for_speech(text) == text
