"""The terminal renderer: rich on a TTY, plain everywhere else.

Presentation must never change what a captured transcript says.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harness.logging_ui import Logger
from harness.ui import (Glyphs, Status, Theme, colour_enabled, estimate_cost,
                        human_time, human_tokens)

ESC = "\x1b["


def rich() -> tuple[Logger, io.StringIO]:
    buf = io.StringIO()
    return Logger(stream=buf, rich=True), buf


def plain() -> tuple[Logger, io.StringIO]:
    buf = io.StringIO()
    return Logger(stream=buf, rich=False), buf


# -- colour is opt-in ------------------------------------------------------
def test_no_colour_when_not_a_tty():
    assert colour_enabled(io.StringIO()) is False


def test_no_color_env_wins(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("HARNESS_UI", "rich")

    class TTY(io.StringIO):
        def isatty(self):
            return True

    assert colour_enabled(TTY()) is False


def test_harness_ui_can_force_either_way(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("HARNESS_UI", "rich")
    assert colour_enabled(io.StringIO()) is True
    monkeypatch.setenv("HARNESS_UI", "plain")

    class TTY(io.StringIO):
        def isatty(self):
            return True

    assert colour_enabled(TTY()) is False


def test_dumb_terminal_gets_plain(monkeypatch):
    monkeypatch.delenv("HARNESS_UI", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")

    class TTY(io.StringIO):
        def isatty(self):
            return True

    assert colour_enabled(TTY()) is False


# -- plain output is unchanged ---------------------------------------------
def test_plain_output_has_no_escape_codes():
    log, buf = plain()
    log.banner()
    log.phase("P1", "route ORACLE")
    log.computed("coverage", "parser.py:11   0.707")
    log.ok("applied", "+2 -0")
    log.degraded("no_coverage", "unavailable")
    log.model_call("m", 100, 20, 1.0)
    assert ESC not in buf.getvalue()


def test_plain_keeps_the_legacy_wording():
    """A presentation change must not alter a captured transcript."""
    log, buf = plain()
    log.set_phase("P4")
    log.computed("lint", "clean", plain="lint: clean - no new diagnostics")
    log.ok("applied", "+2 -0", plain="applied: +2 -0 across 1 file(s)")
    out = buf.getvalue()
    assert "lint: clean - no new diagnostics" in out
    assert "applied: +2 -0 across 1 file(s)" in out
    assert "[P4 VERIFY" in out, "plain mode keeps the phase prefix"


def test_rich_and_plain_carry_the_same_facts():
    facts = ("0.707", "parser.py", "ORACLE")
    outs = []
    for maker in (rich, plain):
        log, buf = maker()
        log.phase("P1", "route ORACLE")
        log.computed("coverage", "parser.py:11   0.707",
                     plain="coverage parser.py:11   0.707 ORACLE")
        outs.append(buf.getvalue())
    for fact in facts:
        assert all(fact in o for o in outs), fact


# -- the provenance gutter -------------------------------------------------
def test_computed_and_model_use_different_glyphs():
    log, buf = rich()
    log.computed("baseline", "3 passed")
    log.model_call("opus", 100, 20, 1.0, extra="synthesis")
    out = buf.getvalue()
    assert Glyphs.COMPUTED in out, "a measured claim must be marked as such"
    assert Glyphs.MODEL in out, "a model claim must be marked as such"
    assert out.index(Glyphs.COMPUTED) < out.index(Glyphs.MODEL)


def test_verdict_glyphs():
    log, buf = rich()
    log.ok("oracle", "PASS")
    log.fail("oracle", "FAIL")
    log.warn("careful")
    out = buf.getvalue()
    assert Glyphs.OK in out and Glyphs.BAD in out and Glyphs.WARN in out


def test_the_legend_explains_the_gutter():
    log, buf = rich()
    log.legend()
    out = buf.getvalue()
    assert "computed" in out and "model" in out
    assert "where it came from" in out


def test_no_legend_in_plain_mode():
    log, buf = plain()
    log.legend()
    assert buf.getvalue() == ""


# -- secrets are still redacted in every mode ------------------------------
@pytest.mark.parametrize("maker", [rich, plain])
def test_redaction_survives_the_renderer(maker):
    log, buf = maker()
    key = "sk-ant-api03-FAKELIVEKEY1234567890"
    log.secrets = [key]
    log.computed("auth", f"header {key}")
    log.kv("key", key)
    assert key not in buf.getvalue()


# -- the live status line --------------------------------------------------
def test_status_is_inert_when_colour_is_off():
    buf = io.StringIO()
    s = Status(buf, Theme(False))
    s.set("working")
    s.clear()
    s.stop()
    assert buf.getvalue() == ""


def test_status_erases_itself_before_a_permanent_line():
    log, buf = rich()
    log.working("verifying")
    log.computed("lint", "clean")
    log.close()
    out = buf.getvalue()
    assert "\x1b[2K" in out, "the status line must be erased"
    assert out.rstrip().endswith("clean")


def test_status_never_appears_in_plain_mode():
    log, buf = plain()
    log.working("verifying")
    log.computed("lint", "clean")
    log.close()
    assert "\x1b" not in buf.getvalue()


def test_counters_are_appended_when_bound():
    log, buf = rich()
    log.bind_counters(lambda: "   4s · 12.0k tokens")
    log.working("implementing")
    log.close()
    assert "12.0k tokens" in buf.getvalue()


def test_a_failing_counter_never_breaks_the_line():
    log, buf = rich()

    def boom():
        raise RuntimeError("counter exploded")

    log.bind_counters(boom)
    log.working("implementing")
    log.close()
    assert "implementing" in buf.getvalue()


# -- formatting helpers ----------------------------------------------------
@pytest.mark.parametrize("n,expected", [
    (5, "5"), (999, "999"), (1500, "1.5k"), (42000, "42.0k"),
    (2_500_000, "2.5M")])
def test_human_tokens(n, expected):
    assert human_tokens(n) == expected


@pytest.mark.parametrize("s,expected", [
    (5, "5s"), (59, "59s"), (60, "1m00s"), (125, "2m05s")])
def test_human_time(s, expected):
    assert human_time(s) == expected


def test_cost_estimate_is_model_aware():
    opus = estimate_cost("claude-opus-5-5", 1_000_000, 0)
    haiku = estimate_cost("claude-haiku-4-5", 1_000_000, 0)
    assert opus and haiku and opus > haiku
    assert estimate_cost("some-unknown-model", 1_000_000, 0) is None


def test_ascii_fallback_when_the_stream_cannot_encode():
    class Ascii(io.StringIO):
        encoding = "ascii"

    log = Logger(stream=Ascii(), rich=True)
    log.unicode = False
    log.g = type("G", (), {"COMPUTED": "*", "MODEL": "o", "PHASE": ">",
                           "OK": "+", "BAD": "x", "WARN": "!", "DOT": "-",
                           "ARROW": "->"})
    log.computed("baseline", "3 passed")
    assert "*" in log.stream.getvalue()
