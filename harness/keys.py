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
        self._buf = ""                    # unconsumed input
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
    def _fill(self, timeout: float | None = None) -> bool:
        """Pull whatever is available into the buffer. True if anything came."""
        if timeout is not None and not select.select([self.fd], [], [],
                                                     timeout)[0]:
            return False
        try:
            data = os.read(self.fd, 64)
        except OSError:
            return False
        if not data:
            return False
        self._buf += data.decode("utf-8", "replace")
        return True

    def read(self) -> str:
        """One keypress.

        Parsed from a buffer rather than byte by byte, because input arrives
        in bursts: an Esc can land in the same read as the arrow key that
        follows it, and consuming a fixed two bytes after an Esc destroys
        whatever came next.
        """
        while True:
            try:
                key = self._take()
                if key is not None:
                    return key
                if self._buf:
                    continue           # still parsing what we already have
                if not self._fill():
                    return CTRL_C      # the stream closed under us
            except KeyboardInterrupt:
                # cbreak leaves ISIG on, so a real ^C arrives as a signal
                # rather than as a byte -- which made every `key == CTRL_C`
                # branch in the console unreachable by an actual ^C. Deliver
                # it as the key it is. The signal handler restored the
                # terminal on its way through, so raw mode is re-armed here.
                self._buf = ""
                self.enter_raw()
                return CTRL_C

    def _take(self) -> str | None:
        """Consume one key from the buffer, or None if more input is needed."""
        buf = self._buf
        if not buf:
            return None

        ch = buf[0]
        if ch != "\x1b":
            self._buf = buf[1:]
            return {"\x03": CTRL_C, "\x04": CTRL_D, "\r": ENTER,
                    "\n": ENTER, "\t": TAB, "\x7f": BACKSPACE,
                    "\b": BACKSPACE}.get(ch, ch)

        # An escape: either a sequence we know, or a bare Esc.
        if len(buf) == 1:
            # Wait briefly for the rest of a sequence before calling it Esc.
            if self._fill(timeout=0.05) and len(self._buf) > 1:
                return self._take()
            self._buf = buf[1:]
            return ESC
        if buf[1] == "[":
            # A CSI sequence runs until a byte in @-~. Consuming a fixed
            # length instead would leave a tail that reads as junk keys.
            end = None
            for i in range(2, len(buf)):
                if "\x40" <= buf[i] <= "\x7e":
                    end = i
                    break
            if end is None:
                if self._fill(timeout=0.05) and len(self._buf) > len(buf):
                    return self._take()
                self._buf = buf[2:]           # unterminated: treat as Esc
                return ESC
            if end == 2 and buf[2] in ARROWS:
                self._buf = buf[3:]
                return ARROWS[buf[2]]

            # X10 mouse: ESC [ M then THREE raw bytes of button and
            # coordinates. The terminator scan stops at the M, so without
            # this the coordinates are parsed as keypresses -- and a column
            # near 81 encodes as "q", which quit the console on a scroll.
            if end == 2 and buf[2] == "M":
                if len(buf) < 6:
                    if self._fill(timeout=0.05) and len(self._buf) > len(buf):
                        return self._take()
                    self._buf = ""
                    return None
                self._buf = buf[6:]
                return None

            self._buf = buf[end + 1:]         # SGR mouse, focus, anything else
            return None
        # Esc followed by something that is not a sequence: a real Esc, and
        # the next key is left in the buffer where it belongs.
        self._buf = buf[1:]
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


def _diagnose() -> None:                          # pragma: no cover - manual
    """`python -m harness.keys` -- show what this terminal actually sends.

    The console disables every mouse mode, so the wheel should produce
    nothing at all here. If scrolling prints anything, this terminal ignores
    those resets, and the bytes it prints are what the parser must swallow.
    """
    import os
    from . import screen as S

    term = TerminalKeys()
    if not term.available:
        print("not a terminal")
        return
    sys.stdout.write(S.MOUSE_OFF)
    sys.stdout.flush()
    print("Press keys, scroll the wheel, then press q to finish.\r")
    print("A scroll that prints nothing is a scroll that cannot close the "
          "console.\r")
    term.enter_raw()
    try:
        while True:
            raw = os.read(term.fd, 64)
            if not raw:
                break
            text = raw.decode("utf-8", "replace")
            shown = text.replace("\x1b", "<ESC>")
            print(f"  {len(raw):>3} byte(s)  {shown!r:<34} "
                  f"{[hex(b) for b in raw]}\r")
            if text == "q":
                break
    except (KeyboardInterrupt, OSError):
        pass
    finally:
        term.close()
        sys.stdout.write("\x1b[?1007h")
        sys.stdout.flush()


if __name__ == "__main__":                        # pragma: no cover - manual
    _diagnose()
