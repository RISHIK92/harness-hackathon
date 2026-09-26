"""Raw-mode key reading, with a guaranteed restore (SPEC.md 11.1).

Leaving a terminal in raw mode is the worst thing a CLI can do to someone:
no echo, no line editing, and no obvious way back. The original attributes
are captured once and restored from a `finally`, from `atexit`, and from the
signal handlers, so every exit path goes through a restore.

The reader is an object rather than a function so tests can drive the console
with a scripted key sequence and no terminal at all.
"""
from __future__ import annotations

import atexit
import io
import os
import select
import signal
import sys

UP, DOWN, LEFT, RIGHT = "up", "down", "left", "right"
ENTER, TAB, ESC, BACKSPACE = "enter", "tab", "esc", "backspace"
CTRL_C, CTRL_D = "ctrl-c", "ctrl-d"

ARROWS = {"A": UP, "B": DOWN, "C": RIGHT, "D": LEFT}


class KeySource:
    """Interface. `read()` returns a key name or a single character."""

    def read(self) -> str:                        # pragma: no cover - iface
        raise NotImplementedError

    def enter_raw(self) -> None:
        """No-op for sources that need no terminal state."""

    def close(self) -> None:
        pass


class ScriptedKeys(KeySource):
    """A test double: hands back a fixed sequence, then quits."""

    def __init__(self, keys) -> None:
        self.keys = list(keys)
        self.reads = 0

    def read(self) -> str:
        self.reads += 1
        if not self.keys:
            return "q"
        return self.keys.pop(0)


class TerminalKeys(KeySource):
    """POSIX raw-mode reader. Restores the terminal on every exit path."""

    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stdin
        try:
            self.fd = self.stream.fileno()
        except (AttributeError, ValueError, OSError, io.UnsupportedOperation):
            self.fd = -1          # not a real stream: unavailable, not fatal
        self._saved = None
        self._registered = False
        try:
            import termios
            import tty
            self._termios, self._tty = termios, tty
        except ImportError:                       # pragma: no cover
            self._termios = self._tty = None

    @property
    def available(self) -> bool:
        if self._termios is None or self.fd < 0:
            return False
        try:
            return bool(self.stream.isatty())
        except (AttributeError, ValueError):
            return False

    def __enter__(self) -> "TerminalKeys":
        self.enter_raw()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def enter_raw(self) -> None:
        if not self.available or self._saved is not None:
            return
        self._saved = self._termios.tcgetattr(self.fd)
        self._tty.setcbreak(self.fd)
        if not self._registered:
            atexit.register(self.close)
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                try:
                    previous = signal.getsignal(sig)
                    signal.signal(sig, self._make_handler(previous))
                except (ValueError, OSError):     # not the main thread
                    pass
            self._registered = True

    def _make_handler(self, previous):
        def handler(signum, frame):
            self.close()
            if callable(previous):
                previous(signum, frame)
            else:
                raise KeyboardInterrupt
        return handler

    def close(self) -> None:
        """Restore the terminal. Runs from atexit and from signal handlers,
        so it must not raise: a failure here would mask whatever is actually
        ending the process, and the terminal would stay broken either way."""
        if self._saved is None or self._termios is None:
            return
        try:
            self._termios.tcsetattr(self.fd, self._termios.TCSADRAIN,
                                    self._saved)
        except (OSError, ValueError, self._termios.error):
            pass          # the fd is gone or was never a terminal
        finally:
            self._saved = None

    # -- reading -----------------------------------------------------------
    def read(self) -> str:
        ch = os.read(self.fd, 1).decode("utf-8", "replace")
        if ch == "\x03":
            return CTRL_C
        if ch == "\x04":
            return CTRL_D
        if ch in ("\r", "\n"):
            return ENTER
        if ch == "\t":
            return TAB
        if ch in ("\x7f", "\b"):
            return BACKSPACE
        if ch != "\x1b":
            return ch

        # an escape sequence, or a bare Esc if nothing follows promptly
        if not select.select([self.fd], [], [], 0.05)[0]:
            return ESC
        rest = os.read(self.fd, 2).decode("utf-8", "replace")
        if rest.startswith("[") and rest[1:2] in ARROWS:
            return ARROWS[rest[1]]
        return ESC


def reader(stream=None) -> KeySource:
    """A terminal reader, already in raw mode, or a scripted quit.

    Entering raw mode here rather than leaving it to the caller is the whole
    point: a reader that is not in cbreak mode gives line-buffered input with
    echo on, so arrow keys print `^[[B` and nothing responds until Enter.
    """
    term = TerminalKeys(stream)
    if term.available:
        term.enter_raw()
        return term
    return ScriptedKeys([])
