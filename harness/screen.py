"""Full-screen terminal ownership for the interactive console.

The alternate screen buffer is entered, the frame is composed and written
with absolute cursor positioning, and every line is truncated to the terminal
width. Truncation is the point: the erase-and-redraw approach it replaces
counted logical lines, so a line that wrapped left a stale copy behind.

On close the alternate buffer is left and the transcript is written to the
normal screen, so nothing that happened is lost to scrollback -- a
full-screen UI that swallows its own output would be worse than no UI.

Restoring the terminal is guaranteed the same way raw mode is: from a
finally, from atexit, and from the signal handlers.
"""
from __future__ import annotations

import atexit
import os
import re
import shutil
import signal
import sys
import threading

ALT_ON = "\x1b[?1049h"
ALT_OFF = "\x1b[?1049l"
CURSOR_OFF = "\x1b[?25l"
CURSOR_ON = "\x1b[?25b".replace("b", "h")
CLEAR = "\x1b[2J\x1b[H"

# The wheel must not be able to reach the key parser at all.
#
# A full-screen app inherits whatever mouse state the terminal was left in by
# whatever ran before it. Two inherited modes each break the frame:
#
#   * mouse reporting (1000/1002/1003, with 1005/1006/1015 encodings) makes a
#     scroll arrive as an escape sequence whose payload bytes are raw -- at
#     column 81 the column byte is literally "q", which quit the console.
#   * alternate scroll (1007) turns the wheel into Up/Down arrow keys, which
#     move the selection under the operator without them touching a key.
#
# So we do not guess: we turn every one of them off on the way in. The wheel
# then produces no input whatsoever, and the parser's mouse handling is left
# as a second line of defence for terminals that ignore these.
MOUSE_OFF = ("\x1b[?1000l\x1b[?1002l\x1b[?1003l"
             "\x1b[?1005l\x1b[?1006l\x1b[?1015l\x1b[?1007l")
# 1007 only has any effect inside the alternate buffer, which we are leaving,
# so restoring the common default is safe and keeps normal scrollback usable.
MOUSE_RESTORE = "\x1b[?1007h"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

MIN_ROWS = 12
MIN_COLS = 40
SCROLLBACK = 2000


def visible_width(text: str) -> int:
    return len(ANSI.sub("", text))


def truncate(text: str, width: int) -> str:
    """Cut to `width` visible characters, keeping the escape codes intact."""
    if visible_width(text) <= width:
        return text
    out, shown = [], 0
    i = 0
    while i < len(text) and shown < width:
        m = ANSI.match(text, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        out.append(text[i])
        shown += 1
        i += 1
    return "".join(out) + "\x1b[0m"


class Screen:
    """Owns the terminal while the console is up."""

    def __init__(self, stream=None, theme=None, unicode_ok: bool = True
                 ) -> None:
        self.stream = stream or sys.stdout
        self.theme = theme
        self.unicode = unicode_ok
        self.body: list = []
        self.header: list = []
        self.panel: list | None = None
        self.status: str = ""
        self.open_ = False
        self._registered = False
        # The status line is repainted by a ticker thread while a phase runs,
        # so composing a frame and writing it has to be atomic -- two
        # interleaved cursor-positioned writes produce a scrambled screen.
        self._lock = threading.RLock()
        self._rows, self._cols = self._size()

    # -- lifecycle ---------------------------------------------------------
    @property
    def usable(self) -> bool:
        try:
            if not self.stream.isatty():
                return False
        except (AttributeError, ValueError):
            return False
        rows, cols = self._size()
        return rows >= MIN_ROWS and cols >= MIN_COLS

    def _size(self) -> tuple:
        try:
            size = shutil.get_terminal_size(fallback=(80, 24))
            return size.lines, size.columns
        except (OSError, ValueError):
            return 24, 80

    def open(self) -> bool:
        if self.open_ or not self.usable:
            return False
        self._rows, self._cols = self._size()
        self._write(ALT_ON + CURSOR_OFF + MOUSE_OFF + CLEAR)
        self.open_ = True
        if not self._registered:
            atexit.register(self.close)
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                try:
                    previous = signal.getsignal(sig)
                    signal.signal(sig, self._handler(previous))
                except (ValueError, OSError):
                    pass
            try:
                signal.signal(signal.SIGWINCH, self._on_resize)
            except (AttributeError, ValueError, OSError):
                pass
            self._registered = True
        return True

    def _handler(self, previous):
        def handle(signum, frame):
            self.close()
            if callable(previous):
                previous(signum, frame)
            else:
                raise KeyboardInterrupt
        return handle

    def _on_resize(self, *_a) -> None:
        self._rows, self._cols = self._size()
        if self.open_:
            try:
                self.render()
            except OSError:
                pass

    def close(self) -> str:
        """Leave the alternate buffer and return the transcript."""
        if not self.open_:
            return "\n".join(self.body)
        self._write(CURSOR_ON + MOUSE_RESTORE + ALT_OFF)
        self.open_ = False
        return "\n".join(self.body)

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)
            self.stream.flush()
        except (OSError, ValueError):
            self.open_ = False

    # -- content -----------------------------------------------------------
    def set_header(self, lines: list) -> None:
        self.header = list(lines)

    def set_panel(self, lines: list | None) -> None:
        """A fixed panel (a menu) instead of the scrolling body."""
        self.panel = list(lines) if lines is not None else None

    def set_status(self, text: str) -> None:
        self.status = text

    def append(self, line: str) -> None:
        for part in str(line).split("\n"):
            self.body.append(part)
        if len(self.body) > SCROLLBACK:
            del self.body[:-SCROLLBACK]

    def transcript(self) -> str:
        return "\n".join(self.body)

    # -- drawing -----------------------------------------------------------
    def render(self) -> None:
        with self._lock:
            self._render_locked()

    def _render_locked(self) -> None:
        if not self.open_:
            return
        rows, cols = self._rows, self._cols
        header = [truncate(h, cols) for h in self.header]
        footer = []
        if self.status:
            footer.append(truncate(self.status, cols))

        room = max(1, rows - len(header) - len(footer) - 1)
        source = self.panel if self.panel is not None else self.body
        window = source[-room:] if len(source) > room else list(source)

        out = ["\x1b[H"]
        row = 1
        for line in header:
            out.append(f"\x1b[{row};1H\x1b[2K{line}")
            row += 1
        for line in window:
            out.append(f"\x1b[{row};1H\x1b[2K{truncate(line, cols)}")
            row += 1
        while row <= rows - len(footer):
            out.append(f"\x1b[{row};1H\x1b[2K")
            row += 1
        for line in footer:
            out.append(f"\x1b[{rows - len(footer) + footer.index(line)};1H"
                       f"\x1b[2K{line}")
        self._write("".join(out))
