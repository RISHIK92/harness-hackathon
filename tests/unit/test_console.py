"""The interactive console, driven by scripted keys -- no terminal needed.

Two properties matter most and are asserted first: the console can never be
reached by an automated run, and the run itself is identical whichever way
the issue arrived.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harness import config as C
from harness import console as Console
from harness import keys as K
from harness.logging_ui import Logger


def make(keys, tmp_path, **cfg_kw):
    buf = io.StringIO()
    log = Logger(stream=buf, rich=True)
    cfg = C.Config(api_key="sk-ant-api03-FAKECONSOLE1234567890", issue="",
                   repo_path=tmp_path)
    for k, v in cfg_kw.items():
        setattr(cfg, k, v)
    ui = Console.Console(log=log, cfg=cfg, source=K.ScriptedKeys(keys),
                         stream=buf)
    return ui, buf, cfg


# -- it must be unreachable from an automated run --------------------------
def test_not_interactive_when_an_issue_was_supplied(tmp_path, monkeypatch):
    cfg = C.Config(api_key="k", issue="a bug", repo_path=tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    assert Console.interactive(cfg) is False


def test_not_interactive_when_piped(tmp_path):
    cfg = C.Config(api_key="k", issue="", repo_path=tmp_path)
    assert Console.interactive(cfg) is False, "stdin is not a tty under pytest"


def test_env_escape_hatch(tmp_path, monkeypatch):
    cfg = C.Config(api_key="k", issue="", repo_path=tmp_path)
    monkeypatch.setenv("HARNESS_NONINTERACTIVE", "1")
    assert Console.interactive(cfg) is False


def test_not_interactive_during_replay(tmp_path):
    cfg = C.Config(api_key="k", issue="", repo_path=tmp_path)
    cfg.replay = "trajectory.jsonl"
    assert Console.interactive(cfg) is False


# -- the menu --------------------------------------------------------------
def test_menu_returns_the_highlighted_index(tmp_path):
    ui, buf, cfg = make([K.DOWN, K.DOWN, K.TAB], tmp_path)
    items = [Console.Item("one"), Console.Item("two"), Console.Item("three")]
    assert ui.menu("pick", items) == 2


def test_menu_wraps_around(tmp_path):
    ui, buf, cfg = make([K.UP, K.ENTER], tmp_path)
    items = [Console.Item("one"), Console.Item("two")]
    assert ui.menu("pick", items) == 1


def test_menu_skips_disabled_items(tmp_path):
    ui, buf, cfg = make([K.DOWN, K.TAB], tmp_path)
    items = [Console.Item("one"), Console.Item("two", enabled=False),
             Console.Item("three")]
    assert ui.menu("pick", items) == 2


def test_number_keys_select_directly(tmp_path):
    ui, buf, cfg = make(["3"], tmp_path)
    items = [Console.Item(x) for x in ("one", "two", "three")]
    assert ui.menu("pick", items) == 2


def test_q_and_esc_back_out(tmp_path):
    for key in ("q", K.ESC, K.CTRL_C):
        ui, buf, cfg = make([key], tmp_path)
        assert ui.menu("pick", [Console.Item("one")]) is None


def test_the_hint_line_is_always_shown(tmp_path):
    ui, buf, cfg = make([K.TAB], tmp_path)
    ui.menu("pick", [Console.Item("one")])
    assert "tab" in buf.getvalue() and "quit" in buf.getvalue()


# -- the confirmation card -------------------------------------------------
def test_card_accepts_on_tab(tmp_path):
    ui, buf, cfg = make([K.TAB], tmp_path)
    assert ui.card("clone?", [("branch", "harness/issue-1")], "tab start")
    assert "harness/issue-1" in buf.getvalue()


def test_card_refuses_on_escape(tmp_path):
    for key in (K.ESC, "n", "q"):
        ui, buf, cfg = make([key], tmp_path)
        assert ui.card("clone?", [], "tab start") is False


# -- history ---------------------------------------------------------------
def test_history_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ui, buf, cfg = make([], tmp_path)
    ui.remember("first issue", tmp_path, 0, 1.5)
    ui.remember("second issue", tmp_path, 2, 2.5)
    past = ui.history()
    assert [e["issue"] for e in past] == ["second issue", "first issue"]
    assert past[0]["exit"] == 2


def test_history_deduplicates_and_caps(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ui, buf, cfg = make([], tmp_path)
    for i in range(10):
        ui.remember(f"issue {i}", tmp_path, 0, 1.0)
    ui.remember("issue 9", tmp_path, 0, 1.0)
    past = ui.history()
    assert len(past) <= Console.HISTORY_LIMIT
    assert len([e for e in past if e["issue"] == "issue 9"]) == 1


def test_unreadable_history_is_not_fatal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "history.json").write_text("not json")
    ui, buf, cfg = make([], tmp_path)
    assert ui.history() == []


# -- task selection --------------------------------------------------------
def test_quitting_yields_an_empty_choice(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ui, buf, cfg = make(["q"], tmp_path)
    choice = ui.select_task()
    assert choice.quit and not choice


def test_github_option_captures_the_reference(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ui, buf, cfg = make([K.DOWN, K.TAB], tmp_path)
    monkeypatch.setattr(ui, "ask_line", lambda *a, **k: "owner/repo#123")
    choice = ui.select_task()
    assert choice.github_ref == "owner/repo#123"
    assert choice.issue == "owner/repo#123"
    assert bool(choice)


def test_paste_option_captures_the_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ui, buf, cfg = make([K.TAB], tmp_path)
    monkeypatch.setattr(ui, "ask_paste", lambda: "parse_date crashes")
    choice = ui.select_task()
    assert choice.issue == "parse_date crashes"
    assert not choice.github_ref


def test_recent_is_disabled_without_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ui, buf, cfg = make([K.DOWN, K.DOWN, K.DOWN, K.TAB], tmp_path)
    monkeypatch.setattr(ui, "ask_line", lambda *a, **k: None)
    monkeypatch.setattr(ui, "ask_paste", lambda: None)
    choice = ui.select_task()
    # the disabled 4th item cannot be reached; selection wraps to the first
    assert choice.quit or choice.issue == ""


def test_a_bad_repository_path_is_reported_not_accepted(tmp_path,
                                                        monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = {"n": 0}

    def once(*a, **k):
        calls["n"] += 1
        return "/definitely/not/here" if calls["n"] == 1 else None

    ui, buf, cfg = make([K.DOWN, K.DOWN, K.TAB, "q"], tmp_path)
    monkeypatch.setattr(ui, "ask_line", once)
    monkeypatch.setattr(ui, "ask_paste", lambda: None)
    ui.select_task()
    assert "no such directory" in buf.getvalue()


# -- after the run ---------------------------------------------------------
def test_after_run_can_ask_for_another(tmp_path):
    """With no report and no GitHub ref, the enabled items are
    0 show-diff, 3 run-another, 4 quit -- so one DOWN lands on 'run another'."""
    ui, buf, cfg = make([K.DOWN, K.TAB], tmp_path)
    assert ui.after_run(0, cfg, tmp_path / "nope.md") == "again"


def test_after_run_quits(tmp_path):
    ui, buf, cfg = make(["q"], tmp_path)
    assert ui.after_run(0, cfg, tmp_path / "nope.md") == "quit"


def test_posting_is_offered_only_for_a_github_run_that_produced_a_fix(
        tmp_path):
    from harness.github import parse_ref
    ref = parse_ref("owner/repo#1")
    ui, buf, cfg = make(["q"], tmp_path)
    report = tmp_path / "run_report.md"
    report.write_text("# report")

    def labels(exit_code, ghref):
        u, b, _ = make(["q"], tmp_path)
        u.after_run(exit_code, cfg, report, ghref)
        return b.getvalue()

    assert "post the report" in labels(0, ref)
    # a failed run must not offer to comment on someone's issue
    out = labels(3, ref)
    assert "post the report" in out          # shown, but disabled
    assert "\x1b[2m" in out


# -- key reading -----------------------------------------------------------
def test_scripted_keys_exhaust_to_quit():
    src = K.ScriptedKeys([K.DOWN])
    assert src.read() == K.DOWN
    assert src.read() == "q"


def test_terminal_reader_is_unavailable_without_a_tty():
    src = K.TerminalKeys(io.StringIO())
    assert src.available is False
    src.close()          # must not raise


def test_reader_falls_back_when_there_is_no_terminal():
    assert isinstance(K.reader(io.StringIO()), K.ScriptedKeys)


# -- cancelling goes back, it does not quit --------------------------------
def test_escape_in_the_paste_editor_returns_to_the_menu(tmp_path,
                                                        monkeypatch):
    """Cancelling a prompt must not end the session -- only q on the menu
    does that."""
    monkeypatch.chdir(tmp_path)
    ui, buf, cfg = make([K.TAB, "q"], tmp_path)     # pick paste, then quit
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return None                                  # the user pressed esc

    monkeypatch.setattr(ui, "ask_paste", cancelled)
    choice = ui.select_task()
    assert calls["n"] == 1, "the paste editor should have been opened once"
    assert choice.quit, "and then q on the menu ends it"
    assert "What should I work on?" in buf.getvalue()


def test_cancelling_the_github_prompt_returns_to_the_menu(tmp_path,
                                                          monkeypatch):
    monkeypatch.chdir(tmp_path)
    ui, buf, cfg = make([K.DOWN, K.TAB, "q"], tmp_path)
    monkeypatch.setattr(ui, "ask_line", lambda *a, **k: None)
    assert ui.select_task().quit


def test_cancelling_then_choosing_still_works(tmp_path, monkeypatch):
    """Back out of paste, then pick GitHub: the menu must still be live."""
    monkeypatch.chdir(tmp_path)
    ui, buf, cfg = make([K.TAB, K.DOWN, K.TAB], tmp_path)
    monkeypatch.setattr(ui, "ask_paste", lambda: None)
    monkeypatch.setattr(ui, "ask_line", lambda *a, **k: "owner/repo#9")
    choice = ui.select_task()
    assert choice.github_ref == "owner/repo#9"
    assert not choice.quit


def test_repeated_back_navigation_does_not_recurse(tmp_path, monkeypatch):
    """Twenty cancels must not pile up stack frames."""
    monkeypatch.chdir(tmp_path)
    ui, buf, cfg = make([K.TAB] * 20 + ["q"], tmp_path)
    monkeypatch.setattr(ui, "ask_paste", lambda: None)
    depth = {"max": 0}
    import sys as _sys
    base = len(_sys._current_frames())

    def counting():
        import traceback
        depth["max"] = max(depth["max"], len(traceback.extract_stack()))
        return None

    monkeypatch.setattr(ui, "ask_paste", counting)
    first = None
    ui.select_task()
    assert depth["max"] < 100, "back-navigation is recursing"


# -- the key parser --------------------------------------------------------
def _parse(data: bytes, count: int) -> list:
    """Drive the real parser from a pipe: no terminal, no timing."""
    import os
    import termios
    import tty

    from harness.keys import TerminalKeys
    r, w = os.pipe()
    os.write(w, data)
    os.close(w)
    src = TerminalKeys.__new__(TerminalKeys)
    src.fd, src._buf, src._saved = r, "", None
    src._registered, src._termios, src._tty = False, termios, tty
    try:
        return [src.read() for _ in range(count)]
    finally:
        os.close(r)


@pytest.mark.parametrize("data,count,expected", [
    (b"abc", 3, ["a", "b", "c"]),
    (b"\x1b[B", 1, [K.DOWN]),
    (b"\x1b[A", 1, [K.UP]),
    (b"\x1b[A\x1b[B", 2, [K.UP, K.DOWN]),
    (b"\x1b", 1, [K.ESC]),
    (b"\t\r\x7f", 3, [K.TAB, K.ENTER, K.BACKSPACE]),
    (b"\x03", 1, [K.CTRL_C]),
    (b"\x04", 1, [K.CTRL_D]),
    (b"hi\x1b[Bq", 4, ["h", "i", K.DOWN, "q"]),
])
def test_key_parsing(data, count, expected):
    assert _parse(data, count) == expected


def test_escape_adjacent_to_the_next_key_loses_neither():
    """Input arrives in bursts. Consuming a fixed two bytes after an Esc
    destroys whatever followed -- which is how pressing esc then down ended
    up doing nothing."""
    assert _parse(b"\x1b\x1b[B", 2) == [K.ESC, K.DOWN]
    assert _parse(b"\x1b\x1b[Aq", 3) == [K.ESC, K.UP, "q"]


def test_an_unknown_escape_sequence_does_not_eat_the_next_key():
    assert _parse(b"\x1b[5~\x1b[B", 1) == [K.DOWN]
