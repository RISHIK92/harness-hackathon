import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from language import detect_language, greeting_for, language_name

CASES = [
    ("why does pricing have a special case for Bangalore?", "en-IN"),
    ("Northwind's webhooks are failing", "en-IN"),
    ("", "en-IN"),
    ("   ...  123 ", "en-IN"),                              # no letters at all
    ("బెంగళూరు ధరలు ఎందుకు వేరుగా ఉన్నాయి?", "te-IN"),
    ("பெங்களூரு விலை ஏன் வித்தியாசமாக உள்ளது?", "ta-IN"),
    ("बैंगलोर की कीमत अलग क्यों है?", "hi-IN"),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_detects_script(text, expected):
    assert detect_language(text) == expected


def test_code_mixed_speech_answers_in_the_indic_language():
    # Real Indian support-call speech: Telugu grammar, English nouns.
    assert detect_language("sir webhook fail అవుతోంది ఎందుకు") == "te-IN"


def test_one_stray_indic_word_does_not_flip_a_long_english_sentence():
    text = "Can you check the webhook delivery log for Northwind Logistics please సర్"
    assert detect_language(text) == "en-IN"


def test_names_and_greetings_exist_for_the_four_target_languages():
    for code, name in [("te-IN", "Telugu"), ("ta-IN", "Tamil"), ("hi-IN", "Hindi"), ("en-IN", "English")]:
        assert language_name(code) == name
        assert greeting_for(code) and greeting_for(code) != greeting_for("xx-XX") or code == "en-IN"


def test_unknown_code_falls_back_to_english():
    assert language_name("zz-ZZ") == "English"
    assert greeting_for("zz-ZZ") == greeting_for("en-IN")
