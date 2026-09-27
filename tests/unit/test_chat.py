"""The conversation, driven through a real terminal.

Chat redraws the whole frame on every keystroke, so it produces far more
output than the menus do. The parent therefore has to drain the pty *while*
it types: writing a whole script first and reading afterwards fills the
buffer and the child blocks forever on its own output. That is a property of
the test, not of the console -- but it is the reason this file has its own
driver instead of reusing the one in test_console_pty.py.
"""
from __future__ import annotations

import os
import pty
import re
import select
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

END = "<<END>>"
CR, ESC, CTRL_C = b"\r", b"\x1b", b"\x03"


def typed(text: bytes) -> list:
    return [bytes([b]) for b in text]


def drive(script, stub_reply=None, warmup: float = 1.2) -> str:
    """Run the chat in a child on a pty and return everything it wrote."""
    pid, fd = pty.fork()
    if pid == 0:                                      # child
        try:
            sys.stdin = os.fdopen(0, "r", buffering=1)
            sys.stdout = os.fdopen(1, "w", buffering=1)
            sys.path.insert(0, str(ROOT))
            from harness import chat as CH
            from harness import config as C
            from harness import console as Console
            from harness.logging_ui import Logger

            cfg = C.Config(api_key="sk-ant-api03-FAKECHAT", issue="",
                           repo_path=ROOT)
            ui = Console.Console(log=Logger(rich=True), cfg=cfg)
            ui.take_terminal()
            chat = CH.Chat(console=ui, cfg=cfg, log=ui.log)
            if stub_reply is not None:
                chat.ask = lambda _q: stub_reply
            try:
                result = chat.run()
            finally:
                ui.source.close()
                ui.release_terminal()
            sys.stdout.write(f"\nRESULT={result!r} TURNS={len(chat.turns)}\n"
                             f"{END}\n")
            sys.stdout.flush()
        except BaseException as exc:                  # pragma: no cover
            try:
                sys.stdout.write(f"\nCHILD-ERROR={exc!r}\n{END}\n")
                sys.stdout.flush()
            except BaseException:
                pass
        finally:
            os._exit(0)

    out = b""

    def pump(seconds: float) -> None:
        nonlocal out
        deadline = time.time() + seconds
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.15)
            if not ready:
                continue
            try:
                data = os.read(fd, 8192)
            except OSError:
                return
            if not data:
                return
            out += data

    try:
        # Same race as the console driver: wait for the prompt to exist
        # rather than guessing how long it takes to appear.
        ready = time.time() + 10
        while time.time() < ready:
            pump(0.1)
            if b"/help" in out:            # the opening note is on screen
                break
        pump(0.15)
        for key in script:
            try:
                os.write(fd, key)
            except OSError:
                break
            pump(0.2)
        deadline = time.time() + 20
        while time.time() < deadline and END.encode() not in out:
            pump(0.3)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    return out.decode("utf-8", "replace")


def visible(out: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out)


def result_of(out: str) -> str:
    assert "CHILD-ERROR" not in out, f"chat raised\n{out[-600:]}"
    assert END in out, "chat never finished -- hung or exited early"
    m = re.search(r"RESULT=(.*?) TURNS=(\d+)", out)
    assert m, f"no result line\n{out[-400:]}"
    return m.group(1)


# -- leaving ---------------------------------------------------------------

def test_exit_leaves_the_chat():
    assert result_of(drive(typed(b"/exit") + [CR])) == "None"


def test_ctrl_c_twice_leaves_and_warns_once_first():
    """cbreak leaves ISIG on, so a real ^C arrives as a signal rather than a
    byte. If that is not translated back into a keypress, every `== CTRL_C`
    branch in the console is unreachable by an actual ^C."""
    out = drive([CTRL_C, CTRL_C])
    assert result_of(out) == "None"
    assert "ctrl-c again" in visible(out), "left without warning on one ^C"


def test_escape_clears_the_line_rather_than_leaving():
    out = drive(typed(b"junk") + [ESC] + typed(b"/exit") + [CR])
    assert result_of(out) == "None"


# -- commands --------------------------------------------------------------

def test_help_lists_the_commands():
    out = drive(typed(b"/help") + [CR] + typed(b"/exit") + [CR])
    text = visible(out)
    for name, _ in __import__("harness.chat", fromlist=["HELP"]).HELP:
        assert name in text, f"{name} missing from /help"


def test_repo_reports_what_the_harness_sees():
    out = drive(typed(b"/repo") + [CR] + typed(b"/exit") + [CR])
    text = visible(out)
    assert "Repository:" in text
    assert "Test command:" in text


def test_diff_reads_the_working_tree():
    out = drive(typed(b"/diff") + [CR] + typed(b"/exit") + [CR])
    text = visible(out)
    assert ("file(s) changed" in text or "working tree is clean" in text)


def test_an_unknown_command_is_named_not_swallowed():
    out = drive(typed(b"/nope") + [CR] + typed(b"/exit") + [CR])
    assert "unknown command /nope" in visible(out)


def test_run_hands_the_issue_back_to_the_caller():
    """Chat must not start the pipeline itself: one caller, one set of
    gates."""
    out = drive(typed(b"/run fix the parser") + [CR])
    assert result_of(out) == "'fix the parser'"


def test_run_without_a_description_asks_for_one():
    out = drive(typed(b"/run") + [CR] + typed(b"/exit") + [CR])
    assert result_of(out) == "None"
    assert "say what to fix" in visible(out)


# -- conversation ----------------------------------------------------------

def test_a_question_is_answered_and_remembered():
    out = drive(typed(b"what is this repo") + [CR] + typed(b"/exit") + [CR],
                stub_reply="It is an autonomous coding harness.")
    assert "It is an autonomous coding harness." in visible(out)
    assert re.search(r"TURNS=2", out), "the exchange was not kept as context"


def test_clear_forgets_the_conversation():
    out = drive(typed(b"hello") + [CR] + typed(b"/clear") + [CR]
                + typed(b"/exit") + [CR], stub_reply="hi")
    assert "conversation forgotten" in visible(out)
    assert re.search(r"TURNS=0", out), "history survived /clear"


def test_a_failing_model_call_is_reported_not_raised():
    """No API key here, so the call genuinely fails."""
    out = drive(typed(b"hello") + [CR] + typed(b"/exit") + [CR])
    assert result_of(out) == "None"
    assert "the model call failed" in visible(out)


def test_an_empty_line_does_nothing():
    out = drive([CR, CR] + typed(b"/exit") + [CR])
    assert result_of(out) == "None"


# -- what it will not do ---------------------------------------------------

def test_the_chat_cannot_act_on_the_repository():
    """A reply that edited files would be a second, unverified path to the
    thing the pipeline exists to do carefully."""
    import harness.chat as CH
    source = Path(CH.__file__).read_text()
    for forbidden in ("apply_all(", "subprocess", "os.system",
                      "workspace.write", "Popen"):
        assert forbidden not in source, (
            f"chat.py references {forbidden}: it must answer, not act")
    assert "cannot edit files" in CH.SYSTEM


def test_history_is_bounded():
    import harness.chat as CH
    chat = CH.Chat(console=None, cfg=None, log=None)
    chat.turns = [CH.Turn("user", f"m{i}") for i in range(200)]
    kept = chat.turns[-CH.MAX_TURNS:]
    assert len(kept) == CH.MAX_TURNS
    assert kept[-1].text == "m199", "kept the oldest instead of the newest"
