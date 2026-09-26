"""Drive the real console through a pseudo-terminal.

The scripted-key tests exercise the menu logic but cannot see terminal
state, so they happily passed while the reader was never actually put into
cbreak mode -- arrow keys echoed as `^[[B` and nothing responded until
Enter. Only a pty catches that.
"""
from __future__ import annotations

import os
import pty
import select
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not hasattr(os, "fork"), reason="needs a POSIX fork for pty")

UP = b"\x1b[A"
DOWN = b"\x1b[B"
TAB = b"\t"
ESC_B = b"\x1b"


def _reattach_std():
    """pytest replaces sys.stdin/stdout with capture stubs whose fileno()
    raises. After pty.fork the real pty is on fds 0/1, so point the streams
    back at it -- otherwise the child sees no terminal and the test proves
    nothing."""
    sys.stdin = os.fdopen(0, "r", buffering=1)
    sys.stdout = os.fdopen(1, "w", buffering=1)


def keys(*items) -> list:
    """Split a script into individual keypresses. Sending an Esc in the same
    burst as the next key is not how a human types, and it makes the reader
    look broken when it is not."""
    out = []
    for item in items:
        if item in (UP, DOWN, TAB, ESC_B, b"\r"):
            out.append(item)
        else:
            out.extend(bytes([b]) for b in item)
    return out


def drive(script, settle: float = 0.45, scenario: str = "select") -> str:
    """Run the console in a child attached to a pty; return what it wrote."""
    pid, fd = pty.fork()
    if pid == 0:                                   # child
        try:
            _reattach_std()
            sys.path.insert(0, str(ROOT))
            from harness import config as C
            from harness import console as Console
            from harness.logging_ui import Logger

            cfg = C.Config(api_key="sk-ant-api03-FAKEPTY1234567890",
                           issue="", repo_path=ROOT)
            log = Logger(rich=True)
            ui = Console.Console(log=log, cfg=cfg)
            owned = ui.take_terminal()          # the path production uses
            try:
                if scenario == "after":
                    report = ROOT / "README.md"
                    result = ui.after_run(0, cfg, report, None)
                    tail = f"AFTER={result}"
                else:
                    choice = ui.select_task()
                    tail = (f"CHOICE={choice.github_ref or ''}|"
                            f"{'quit' if choice.quit else 'go'}")
            finally:
                ui.source.close()
                ui.release_terminal()
            sys.stdout.write(f"\nOWNED={owned} {tail}\n")
            sys.stdout.flush()
        except BaseException as exc:               # pragma: no cover
            try:
                sys.stdout.write(f"\nCHILD-ERROR={exc!r}\n")
                sys.stdout.flush()
            except BaseException:
                pass
        finally:
            os._exit(0)                            # never return to pytest

    out = b""
    deadline = time.time() + 12
    try:
        time.sleep(settle)                         # let the menu draw
        chunks = script if isinstance(script, list) else \
            [script[i:i + 1] for i in range(len(script))]
        for chunk in chunks:
            try:
                os.write(fd, chunk)
            except OSError:
                break          # the child already finished
            time.sleep(0.06)
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 0.3)
            if not r:
                if b"CHOICE=" in out or b"CHILD-ERROR" in out:
                    break
                continue
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break
            out += data
            if b"CHOICE=" in out or b"CHILD-ERROR" in out:
                break
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


def test_arrow_keys_are_not_echoed_as_escape_sequences():
    """The regression: ^[[B appearing on screen means raw mode is off."""
    out = drive(keys(DOWN, DOWN, b"q"))
    assert "^[[B" not in out, "arrow keys are being echoed: raw mode is off"
    assert "[B[B" not in out


def test_arrows_navigate_and_the_in_frame_editor_accepts_text():
    """One pass proving three things: the selection moves on the keypress,
    the second item opens the reference editor, and typing inside the frame
    reaches the choice."""
    out = drive(keys(DOWN, TAB, b"a/b#7", TAB))
    assert "CHILD-ERROR" not in out, out[-400:]
    assert "CHOICE=a/b#7|go" in out, out[-400:]


def test_q_quits_immediately():
    out = drive(keys(b"q"))
    assert "CHOICE=|quit" in out, out[-400:]


def test_the_terminal_is_taken_and_given_back():
    """The alternate screen is entered on start and left on exit, so the
    user's scrollback survives."""
    out = drive(keys(b"q"))
    assert "OWNED=True" in out, out[-400:]
    assert "\x1b[?1049h" in out, "the alternate screen was never entered"
    assert "\x1b[?1049l" in out, "the alternate screen was never left"
    assert out.index("\x1b[?1049h") < out.index("\x1b[?1049l")


def test_the_frame_redraws_absolutely_not_by_scrolling():
    """Absolute positioning means no stale copy, however a line wraps."""
    out = drive(keys(DOWN, UP, DOWN, b"q"))
    assert "\x1b[1;1H" in out or "\x1b[H" in out, \
        "the frame should be drawn with absolute cursor positioning"
    assert "\x1b[0J" not in out, \
        "the erase-and-redraw path should not be used once we own the screen"


def test_the_cursor_is_hidden_while_the_frame_is_up():
    out = drive(keys(b"q"))
    assert "\x1b[?25l" in out and "\x1b[?25h" in out


def test_the_terminal_is_restored_on_exit():
    """After the child exits, its terminal attributes must be back."""
    import termios
    pid, fd = pty.fork()
    if pid == 0:                                   # child
        try:
            _reattach_std()
            sys.path.insert(0, str(ROOT))
            from harness import keys as K
            before = termios.tcgetattr(0)
            src = K.reader()
            during = termios.tcgetattr(0)
            src.close()
            after = termios.tcgetattr(0)
            raw_entered = (during[3] & termios.ECHO) != (before[3] &
                                                         termios.ECHO)
            # Byte-equality of the whole struct is too strict: macOS leaves
            # PENDIN (a transient kernel flag) set after cbreak. What must be
            # restored is the state a human depends on -- echo and canonical
            # line editing.
            mask = termios.ECHO | termios.ICANON | termios.ISIG
            restored = (after[3] & mask) == (before[3] & mask)
            sys.stdout.write(f"\nRAW={raw_entered} RESTORED={restored}\n")
            sys.stdout.flush()
        except BaseException as exc:               # pragma: no cover
            try:
                sys.stdout.write(f"\nCHILD-ERROR={exc!r}\n")
                sys.stdout.flush()
            except BaseException:
                pass
        finally:
            os._exit(0)                            # never return to pytest

    out = b""
    deadline = time.time() + 10
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.3)
        if not r:
            continue
        try:
            data = os.read(fd, 4096)
        except OSError:
            break
        if not data:
            break
        out += data
        if b"RAW=" in out or b"CHILD-ERROR" in out:
            break
    try:
        os.close(fd)
        os.waitpid(pid, 0)
    except (OSError, ChildProcessError):
        pass
    text = out.decode("utf-8", "replace")
    assert "RAW=True" in text, f"raw mode was never entered: {text[-300:]}"
    assert "RESTORED=True" in text, f"terminal not restored: {text[-300:]}"


# -- cancelling, for real --------------------------------------------------
def test_escape_leaves_the_paste_editor_and_returns_to_the_menu():
    """The reported bug: esc appeared to do nothing, because cancelling the
    editor quit the whole session instead of going back."""
    # open paste, type, press esc, then pick github and finish
    out = drive(keys(TAB, b"some issue text", ESC_B, DOWN, TAB, b"a/b#3",
                     TAB), settle=0.5)
    assert "CHILD-ERROR" not in out, out[-400:]
    assert "CHOICE=a/b#3|go" in out, \
        "esc should return to the menu, leaving it usable"
    # the menu must have been drawn again after the cancel
    assert out.count("What should I work on?") >= 2, out[-400:]


def test_escape_on_the_menu_itself_quits():
    out = drive(keys(ESC_B), settle=0.5)
    assert "CHOICE=|quit" in out, out[-400:]


def test_typing_is_shown_in_the_editor():
    out = drive(keys(TAB, b"hello world", ESC_B, b"q"), settle=0.5)
    assert "hello world" in out, "typed characters must be echoed in-frame"


# -- the after-run menu ----------------------------------------------------
def test_choosing_an_action_does_not_look_like_the_list_moving():
    """Reported as 'tab and enter move up and down instead of selecting'.

    Two causes: the diff was written straight to the stream, landing at
    whatever cursor position the frame was at, and the menu then restarted
    at the first item.
    """
    out = drive(keys(TAB, ESC_B, b"q"), settle=0.5, scenario="after")
    assert "CHILD-ERROR" not in out, out[-400:]
    assert "AFTER=quit" in out, out[-400:]
    # tab opened the diff pager, in the frame
    assert "esc back" in out, "tab should open a pager, not dump to the stream"


def test_the_cursor_stays_where_it_was_after_an_action():
    """Come back from the diff and the highlight must still be on it."""
    out = drive(keys(TAB, ESC_B, TAB, ESC_B, b"q"), settle=0.5,
                scenario="after")
    assert "CHILD-ERROR" not in out, out[-400:]
    assert out.count("esc back") >= 2, \
        "the second tab should have reopened the same item"


def test_no_post_item_without_a_github_reference():
    out = drive(keys(b"q"), settle=0.5, scenario="after")
    assert "post the report to None" not in out
    assert "post the report" not in out


def test_run_another_is_reachable():
    # items: diff, report(README exists so enabled), run another, quit
    out = drive(keys(DOWN, DOWN, TAB), settle=0.5, scenario="after")
    assert "AFTER=again" in out, out[-400:]
