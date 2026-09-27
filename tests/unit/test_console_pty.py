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
# X10 wheel report: ESC [ M then three raw bytes. At column 81 the column
# byte is "q" -- which is what used to quit the console mid-scroll.
SCROLL = b"\x1b[M" + bytes([64 + 32, ord("q"), 32 + 5])
END = "<<END>>"


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
        if item in (UP, DOWN, TAB, ESC_B, b"\r", SCROLL):
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
            import termios
            before = termios.tcgetattr(0)
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
                elif scenario == "picker":
                    picked = ui.pick_repo()
                    tail = f"PICK={getattr(picked, 'name', None) or picked}"
                elif scenario == "clarify":
                    answer = ui.clarify("Which file should I start from?",
                                        "The issue names no file or symbol.")
                    tail = f"CLARIFY={answer}"
                elif scenario == "consent":
                    ok = ui.consent_card("push", "push the branch",
                                         [("repo", "a/b"), ("branch", "x")])
                    tail = f"CONSENT={ok}"
                elif scenario == "gate_install":
                    # The real Gate, with the real card behind it.
                    from harness import consent
                    gate = consent.Gate(consent.Policy.from_env(), log,
                                        confirm=ui.consent_card)
                    ok = gate.allow(consent.INSTALL,
                                    "install dependencies with npm",
                                    [("command", "npm ci --ignore-scripts")])
                    tail = f"CONSENT={ok} DECIDED={gate.decisions[0][1]}"
                else:
                    choice = ui.select_task()
                    tail = (f"CHOICE={choice.github_ref or ''}|"
                            f"{'quit' if choice.quit else 'go'}")
            finally:
                ui.source.close()
                ui.release_terminal()
            after = termios.tcgetattr(0)
            flags = termios.ECHO | termios.ICANON | termios.ISIG
            restored = (after[3] & flags) == (before[3] & flags)
            sys.stdout.write(f"\nOWNED={owned} {tail} "
                             f"RESTORED={restored} {END}\n")
            sys.stdout.flush()
        except BaseException as exc:               # pragma: no cover
            try:
                sys.stdout.write(f"\nCHILD-ERROR={exc!r} {END}\n")
                sys.stdout.flush()
            except BaseException:
                pass
        finally:
            os._exit(0)                            # never return to pytest

    out = b""
    deadline = time.time() + 12
    try:
        # Wait for the frame, do not guess at it. A fixed sleep is a race:
        # under load the child has not entered raw mode yet, the first
        # keystrokes are echoed by the tty, and the test fails for a reason
        # that has nothing to do with the console.
        ready = time.time() + 8
        while time.time() < ready:
            r, _, _ = select.select([fd], [], [], 0.1)
            if r:
                try:
                    out += os.read(fd, 8192)
                except OSError:
                    break
            # The picker scans the filesystem before drawing, so the header
            # appearing does not mean it is ready for keys. Wait for the
            # screen the scenario is actually about.
            marker = {"picker": b"Local repositories",
                      "consent": b"Permission needed",
                      "gate_install": b"Permission needed",
                      "clarify": b"I need one thing"}.get(scenario, b"HARNESS")
            if marker in out:
                break
        time.sleep(0.1)                            # let the reader settle
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
                if END.encode() in out:
                    break
                continue
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break
            out += data
            if END.encode() in out:
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


# ---------------------------------------------------------------------------
# End-to-end sweep: every screen, every way out.
#
# The bugs this catches are the ones that only appear when a real terminal is
# on the other end -- a key parser that eats the next keystroke, a frame that
# scrolls instead of redrawing, a wheel event read as "q", an exception path
# that leaves the terminal in raw mode. Each case asserts the same three
# invariants, because any one of them failing is a broken CLI.
# ---------------------------------------------------------------------------

SWEEP = [
    ("menu: quit",                "select",  keys(b"q"), "CHOICE=|quit"),
    ("menu: esc quits",           "select",  keys(ESC_B), "CHOICE=|quit"),
    ("menu: arrows then quit",    "select",  keys(DOWN, DOWN, UP, b"q"),
     "CHOICE=|quit"),
    # The wheel must not merely fail to crash -- it must not be read as a
    # keypress at all. So scroll first, then drive the menu to a *distinct*
    # outcome: if the wheel still reached the console as "q", this comes back
    # quit instead of go, and the old bug is caught.
    ("menu: wheel is inert",      "select",
     keys(SCROLL, SCROLL, TAB, b"still here", TAB), "CHOICE=|go"),
    ("paste: esc returns",        "select",  keys(TAB, b"hello", ESC_B, b"q"),
     "CHOICE=|quit"),
    ("paste: submit",             "select",  keys(TAB, b"a bug", TAB),
     "CHOICE=|go"),
    ("github: esc returns",       "select",
     keys(DOWN, TAB, b"a/b#1", ESC_B, b"q"), "CHOICE=|quit"),
    ("github: submit",            "select",  keys(DOWN, TAB, b"a/b#5", TAB),
     "CHOICE=a/b#5|go"),
    ("picker: esc returns",       "picker",  keys(b"harn", ESC_B),
     "PICK=None"),
    ("picker: filter and pick",   "picker",  keys(b"harn", TAB),
     "PICK=harness-hackathon"),
    ("picker: no match, esc",     "picker",  keys(b"zzzzzz", ESC_B),
     "PICK=None"),
    ("consent: allow",            "consent", keys(TAB), "CONSENT=True"),
    ("consent: decline",          "consent", keys(ESC_B), "CONSENT=False"),
    ("after: diff pager",         "after",   keys(TAB, ESC_B, b"q"),
     "AFTER=quit"),
    ("after: report pager",       "after",
     keys(DOWN, TAB, DOWN, DOWN, ESC_B, b"q"), "AFTER=quit"),
    ("after: run another",        "after",   keys(DOWN, DOWN, TAB),
     "AFTER=again"),
    # Same again inside the pager: scroll, leave, then pick a distinct item.
    ("after: wheel inside pager", "after",
     keys(TAB, SCROLL, SCROLL, ESC_B, DOWN, DOWN, TAB), "AFTER=again"),
]


@pytest.mark.parametrize("name,scenario,script,expect",
                         SWEEP, ids=[c[0] for c in SWEEP])
def test_every_screen_survives_and_gives_the_terminal_back(name, scenario,
                                                           script, expect):
    out = drive(script, scenario=scenario)

    assert "CHILD-ERROR" not in out, f"{name}: console raised\n{out[-600:]}"
    assert END in out, f"{name}: never finished -- hung or exited early"
    assert "RESTORED=True" in out, (
        f"{name}: left the terminal in raw mode; a shell after this is "
        f"unusable")
    assert expect in out, (
        f"{name}: expected {expect!r}, which means the keys did not land "
        f"where they should have\n{out[-600:]}")

    # No keypress may reach the screen as a literal escape sequence. Strip the
    # sequences the console itself emits, then look for what is left over.
    import re
    leftover = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out)
    leftover = leftover.replace("\x1b[?1049h", "").replace("\x1b[?1049l", "")
    assert "^[" not in leftover, f"{name}: raw escape echoed to the screen"
    assert "\x1b[B" not in leftover, f"{name}: an arrow key leaked through"


def test_the_real_frame_disables_the_wheel_on_a_real_terminal():
    """The unit test proves the bytes are composed; this proves they are
    actually written to a terminal by the path production uses."""
    out = drive(keys(b"q"), scenario="select")
    assert "\x1b[?1007l" in out, (
        "alternate scroll left enabled: the wheel still arrives as arrow keys")
    assert "\x1b[?1000l" in out and "\x1b[?1006l" in out, (
        "mouse reporting left enabled: the wheel still arrives as bytes")


def test_the_clarifying_question_draws_once_and_returns_the_answer():
    """clarify() draws a panel and then opens the editor, which draws its
    own -- an easy way to end up with the question on screen twice."""
    out = drive(keys(b"src/parser.py", TAB), scenario="clarify")

    assert "CLARIFY=src/parser.py" in out, "the answer was not captured"
    visible = __import__("re").sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out)
    assert visible.count("Which file should I start from?") == 1, \
        "the question was drawn more than once"


def test_skipping_the_question_is_allowed():
    out = drive(keys(ESC_B), scenario="clarify")
    assert "CLARIFY=None" in out, "esc must skip rather than block the run"


# -- the permission gate, driven through a real terminal --------------------

@pytest.mark.parametrize("key,expected,decision", [
    (TAB, "True", "allowed"),
    (b"y", "True", "allowed"),
    (ESC_B, "False", "declined"),
    (b"q", "False", "declined"),
    (b"n", "False", "declined"),
])
def test_the_gate_asks_and_honours_the_answer(key, expected, decision):
    """Installing reaches a registry and runs foreign code, so the answer
    given at the terminal has to be the answer that is acted on."""
    out = drive(keys(key), scenario="gate_install")
    assert f"CONSENT={expected}" in out, f"{key!r} was not honoured"
    assert f"DECIDED={decision}" in out
    visible = __import__("re").sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out)
    assert "Permission needed" in visible, "no card was shown to the operator"


def test_a_decided_policy_does_not_interrupt(monkeypatch):
    """HARNESS_AUTO already answered; asking again would be noise."""
    import os
    os.environ["HARNESS_AUTO"] = "install"
    try:
        out = drive([], scenario="gate_install")
    finally:
        os.environ.pop("HARNESS_AUTO", None)
    assert "CONSENT=True" in out and "DECIDED=auto" in out
    visible = __import__("re").sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out)
    assert "Permission needed" not in visible, "asked despite HARNESS_AUTO"


def test_the_status_line_keeps_ticking_during_a_phase():
    """In the frame, working() used to paint once and return, so the elapsed
    time and token count froze at the instant the phase began -- a
    four-minute verify read the same number throughout."""
    import re as _re

    pid, fd = pty.fork()
    if pid == 0:                                       # pragma: no cover
        try:
            _reattach_std()
            sys.path.insert(0, str(ROOT))
            import time as _time

            from harness import config as C
            from harness import console as Console
            from harness.logging_ui import Logger

            cfg = C.Config(api_key="sk-ant-api03-FAKETICK", issue="",
                           repo_path=ROOT)
            log = Logger(rich=True)
            ui = Console.Console(log=log, cfg=cfg)
            ui.take_terminal()
            started = _time.time()
            log.bind_counters(lambda: f"   {_time.time() - started:.0f}s")
            log.working("verifying")
            _time.sleep(2.6)
            log.done_working()
            ui.release_terminal()
            sys.stdout.write(f"\n{END}\n")
            sys.stdout.flush()
        except BaseException:
            pass
        finally:
            os._exit(0)

    out = b""
    deadline = time.time() + 12
    try:
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 0.2)
            if not r:
                continue
            try:
                data = os.read(fd, 8192)
            except OSError:
                break
            if not data:
                break
            out += data
            if END.encode() in out:
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

    text = out.decode("utf-8", "replace")
    seen = sorted({int(s) for s in _re.findall(r"(\d+)s", text)})
    assert len(seen) >= 3, f"the counter did not advance: saw {seen}"
    assert "verifying" in _re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
