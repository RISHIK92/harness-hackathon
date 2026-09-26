"""WP5: formats, tolerant parsing, the anchor ladder, validation, hygiene."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harness import config as C
from harness.edit import formats as F
from harness.edit import hygiene
from harness.edit.apply import EditFailure, apply_all, locate, render, rollback
from harness.edit.parse import EditParseError, parse
from harness.edit.validate import scope_check, size_check, syntax_check
from harness.records import ChangePlan, FileIntent

SAMPLE = '''def normalize(raw):
    """Strip and lowercase."""
    return raw.strip().lower()


def parse_date(text):
    """Parse YYYY-MM-DD."""
    parts = text.split("-")
    return int(parts[0]), int(parts[1]), int(parts[2])
'''


# -- T5.1 format choice ----------------------------------------------------
@pytest.mark.parametrize("tier,expected", [
    ("T0", F.LINE_RANGE), ("T1", F.SEARCH_REPLACE), ("T2", F.SEARCH_REPLACE)])
def test_format_by_tier(tier, expected):
    assert F.choose(tier, 300) == expected


def test_conservative_always_uses_the_safest_format():
    assert F.choose("T2", 300, conservative=True) == F.LINE_RANGE


def test_format_de_escalates_on_repeated_failure():
    assert F.choose("T2", 300, failures=1) == F.LINE_RANGE
    assert F.choose("T2", 80, failures=2) == F.WHOLE_FILE
    assert F.choose("T2", 3000, failures=2) == F.LINE_RANGE


def test_whole_file_is_refused_for_large_files():
    assert F.de_escalate(F.LINE_RANGE, 3000) == F.LINE_RANGE
    assert F.de_escalate(F.LINE_RANGE, 80) == F.WHOLE_FILE


def test_unified_diff_is_not_implemented():
    assert "UNIFIED" not in dir(F)
    assert set(F.ORDER) == {F.SEARCH_REPLACE, F.LINE_RANGE, F.WHOLE_FILE}


# -- T5.2 tolerant parsing -------------------------------------------------
def test_search_replace_roundtrip():
    text = ("<<<<<<< SEARCH src/a.py\n    return x\n=======\n"
            "    return y\n>>>>>>> REPLACE\n")
    edits = parse(text, F.SEARCH_REPLACE)
    assert len(edits) == 1
    assert edits[0].path == "src/a.py"
    assert edits[0].search == "    return x"
    assert edits[0].replace == "    return y"


def test_parse_tolerates_fences_and_prose():
    text = ("Sure! Here is the fix:\n\n```python\n"
            "<<<<<<< SEARCH src/a.py\nold\n=======\nnew\n>>>>>>> REPLACE\n"
            "```\nHope that helps.\n")
    edits = parse(text, F.SEARCH_REPLACE)
    assert edits[0].search == "old" and edits[0].replace == "new"


def test_parse_repairs_an_unterminated_block():
    text = "<<<<<<< SEARCH src/a.py\nold line\n=======\nnew line"
    edits = parse(text, F.SEARCH_REPLACE)
    assert edits[0].replace == "new line"


def test_parse_multiple_hunks():
    text = ("<<<<<<< SEARCH a.py\n1\n=======\n2\n>>>>>>> REPLACE\n"
            "<<<<<<< SEARCH b.py\n3\n=======\n4\n>>>>>>> REPLACE\n")
    assert [e.path for e in parse(text, F.SEARCH_REPLACE)] == ["a.py", "b.py"]


def test_line_range_block():
    text = "<<<<<<< REPLACE src/a.py:5-7\nnew body\n>>>>>>>\n"
    e = parse(text, F.LINE_RANGE)[0]
    assert (e.start, e.end, e.replace) == (5, 7, "new body")


def test_line_range_accepts_bare_text_against_the_given_range():
    """The harness supplied the range; bare replacement text is valid."""
    e = parse("    return 0\n", F.LINE_RANGE, default_path="a.py",
              default_range=(3, 4))[0]
    assert (e.start, e.end) == (3, 4)
    assert e.replace.strip() == "return 0"


def test_line_range_strips_echoed_line_numbers():
    text = "<<<<<<< REPLACE a.py:1-2\n 1 | def f():\n 2 |     return 1\n>>>>>>>"
    e = parse(text, F.LINE_RANGE)[0]
    assert e.replace == "def f():\n    return 1"


def test_whole_file_block():
    e = parse("<<<<<<< FILE a.py\nwhole thing\n>>>>>>>", F.WHOLE_FILE)[0]
    assert e.replace == "whole thing"


def test_empty_reply_is_a_typed_failure():
    with pytest.raises(EditParseError):
        parse("", F.SEARCH_REPLACE)
    with pytest.raises(EditParseError):
        parse("I could not do it, sorry.", F.SEARCH_REPLACE)


# -- T5.3 the anchor ladder ------------------------------------------------
def test_exact_match():
    s, e = locate(SAMPLE, '    parts = text.split("-")')
    assert SAMPLE[s:e] == '    parts = text.split("-")'


def test_whitespace_normalized_match():
    s, e = locate(SAMPLE, '    parts   =  text.split("-")')
    assert "parts" in SAMPLE[s:e]


def test_indentation_agnostic_match():
    s, e = locate(SAMPLE, 'parts = text.split("-")')
    assert "parts" in SAMPLE[s:e]


def test_fuzzy_match_within_threshold():
    s, e = locate(SAMPLE, '    """Parse YYYY-MM-DD"""')
    assert "Parse YYYY-MM-DD" in SAMPLE[s:e]


def test_ambiguous_match_is_a_failure_never_a_guess():
    text = "def f():\n    return 1\n\n\ndef g():\n    return 1\n"
    with pytest.raises(EditFailure) as exc:
        locate(text, "    return 1")
    assert exc.value.stage == "locate"
    assert "unique" in str(exc.value)


def test_missing_anchor_is_a_failure():
    with pytest.raises(EditFailure):
        locate(SAMPLE, "this text is nowhere in the file")


def test_failure_feedback_is_specific():
    f = EditFailure("locate", "SEARCH text not found in the file",
                    path="src/a.py")
    msg = f.feedback()
    assert "src/a.py" in msg and "byte for byte" in msg
    assert msg != "that didn't work"


# -- apply / rollback ------------------------------------------------------
def test_apply_and_rollback(tmp_path):
    (tmp_path / "a.py").write_text(SAMPLE)
    edit = parse("<<<<<<< SEARCH a.py\n    return raw.strip().lower()\n"
                 "=======\n    return raw.strip().upper()\n>>>>>>> REPLACE",
                 F.SEARCH_REPLACE)[0]
    applied = apply_all(tmp_path, [edit])
    assert "upper()" in (tmp_path / "a.py").read_text()
    assert applied[0].added == 1 and applied[0].removed == 1
    rollback(tmp_path, applied)
    assert (tmp_path / "a.py").read_text() == SAMPLE


def test_multi_file_apply_is_atomic(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    good = parse("<<<<<<< SEARCH a.py\nx = 1\n=======\nx = 9\n>>>>>>> REPLACE",
                 F.SEARCH_REPLACE)[0]
    bad = parse("<<<<<<< SEARCH b.py\nNOT PRESENT\n=======\nz\n>>>>>>> REPLACE",
                F.SEARCH_REPLACE)[0]
    with pytest.raises(EditFailure):
        apply_all(tmp_path, [good, bad])
    assert (tmp_path / "a.py").read_text() == "x = 1\n", "no partial write"


def test_line_range_outside_the_file_is_rejected(tmp_path):
    (tmp_path / "a.py").write_text("one\ntwo\n")
    e = parse("<<<<<<< REPLACE a.py:1-99\nnew\n>>>>>>>", F.LINE_RANGE)[0]
    with pytest.raises(EditFailure) as exc:
        render(e, "one\ntwo\n")
    assert "outside the file" in str(exc.value)


# -- T5.4 validation -------------------------------------------------------
def test_syntax_check_catches_broken_python(tmp_path):
    (tmp_path / "a.py").write_text("def f(:\n    pass\n")
    with pytest.raises(EditFailure) as exc:
        syntax_check(tmp_path, "a.py")
    assert exc.value.stage == "syntax"


def test_syntax_check_passes_valid_python(tmp_path):
    (tmp_path / "a.py").write_text(SAMPLE)
    syntax_check(tmp_path, "a.py")


def test_scope_guard_rejects_forbidden_and_unplanned(tmp_path):
    from harness.edit.apply import Applied
    plan = ChangePlan(fix_description="d",
                      files_to_change=[FileIntent("src/a.py")],
                      files_must_not_change=["tests/test_a.py"])
    with pytest.raises(EditFailure) as exc:
        scope_check([Applied("tests/test_a.py", "", "", 1, 0)], plan)
    assert exc.value.stage == "scope"
    with pytest.raises(EditFailure):
        scope_check([Applied("src/other.py", "", "", 1, 0)], plan)
    scope_check([Applied("src/a.py", "", "", 1, 0)], plan)


def test_size_guard_at_130_percent(tmp_path):
    from harness.edit.apply import Applied
    plan = ChangePlan(fix_description="d",
                      files_to_change=[FileIntent("a.py")],
                      estimated_lines_changed=10)
    size_check([Applied("a.py", "", "", 7, 6)], plan)          # 13 == cap
    with pytest.raises(EditFailure) as exc:
        size_check([Applied("a.py", "", "", 10, 5)], plan)     # 15 > 13
    assert exc.value.stage == "size"


def test_conservative_mode_caps_at_15_lines():
    from harness.edit.apply import Applied
    plan = ChangePlan(fix_description="d",
                      files_to_change=[FileIntent("a.py")],
                      estimated_lines_changed=100)
    with pytest.raises(EditFailure):
        size_check([Applied("a.py", "", "", 16, 0)], plan, conservative=True)


# -- T5.5 hygiene ----------------------------------------------------------
def test_hygiene_flags_only_added_lines():
    before = "# TODO: clean this up someday\ndef f():\n    return 1\n"
    after = "# TODO: clean this up someday\ndef f():\n    return 2\n"
    assert hygiene.scan(before, after, "a.py") == []


def test_hygiene_catches_added_debt():
    before = "def f():\n    return 1\n"
    after = ("def f():\n    print('debug here')\n    # TODO fix later\n"
             "    # old = 1\n    return 2   \n")
    flags = hygiene.scan(before, after, "a.py")
    assert "debug_output" in flags
    assert "todo_marker" in flags
    assert "commented_code" in flags
    assert "trailing_whitespace" in flags


def test_hygiene_catches_unused_import():
    before = "def f():\n    return 1\n"
    after = "import json\n\n\ndef f():\n    return 1\n"
    assert "unused_imports" in hygiene.scan(before, after, "a.py")


def test_hygiene_allows_a_used_import():
    before = "def f():\n    return 1\n"
    after = "import json\n\n\ndef f():\n    return json.dumps({})\n"
    assert "unused_imports" not in hygiene.scan(before, after, "a.py")


def test_hygiene_catches_leftover_edit_markers():
    after = "def f():\n<<<<<<< SEARCH\n    return 1\n"
    assert "edit_markers" in hygiene.scan("def f():\n", after, "a.py")
