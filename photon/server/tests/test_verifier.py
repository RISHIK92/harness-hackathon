"""The verifier is the last line of the 'no uncited claim' rule, so its
marker parsing has to cope with every citation shape the compose model
actually produces — including several ids inside one bracket."""
from app.agent.verifier import verify

VALID = {"ev_80abd768", "ev_33f954d5"}


def test_multi_id_bracket_is_accepted_not_abstained():
    composed = {"answer": "Bangalore is a partner city [ev_80abd768, ev_33f954d5].",
                "claims": [], "abstained": False, "escalation": None}
    out = verify(composed, VALID)
    assert out["abstained"] is False, "a well-cited answer must not be thrown away"


def test_fabricated_id_still_abstains():
    composed = {"answer": "Bangalore is a partner city [ev_deadbeef].",
                "claims": [], "abstained": False, "escalation": None}
    assert verify(composed, VALID)["abstained"] is True


def test_one_real_one_fake_id_in_the_same_bracket_abstains():
    composed = {"answer": "Bangalore is a partner city [ev_80abd768, ev_deadbeef].",
                "claims": [], "abstained": False, "escalation": None}
    assert verify(composed, VALID)["abstained"] is True


def test_no_markers_at_all_abstains():
    composed = {"answer": "Bangalore is a partner city.", "claims": [],
                "abstained": False, "escalation": None}
    assert verify(composed, VALID)["abstained"] is True


def test_non_latin_answer_with_valid_markers_passes():
    composed = {"answer": "బెంగళూరు భాగస్వామి నగరం [ev_80abd768].", "claims": [],
                "abstained": False, "escalation": None}
    assert verify(composed, VALID)["abstained"] is False
