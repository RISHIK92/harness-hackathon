"""Terminal presentation: theme, glyphs, live status (SPEC.md 11.1).

Hand-rolled ANSI, no dependency -- `rich` would be a wheel that can fail on
the evaluator's machine, and this needs about eighty lines.

The visual identity is PROVENANCE. Every line the harness prints carries a
glyph saying where the claim came from:

    ◆  computed   a program produced this: grep, coverage, git, a test run
    ◇  model      a model produced this
    ▸  phase      a phase boundary
    ✓ ✗ !         a verdict

That is not decoration. The harness's whole argument is that most of what it
concludes is measured rather than asked, and the gutter is where you can see
that at a glance -- count the diamonds.

Colour and live status appear only on a TTY. Piped output is plain text, so
a transcript stays diffable and an evaluator's log file stays readable.
"""
from __future__ import annotations

import os
import sys
import threading
import time

# -- theme -----------------------------------------------------------------
RESET = "\x1b[0m"


class Theme:
    """ANSI codes, or empty strings when colour is off."""

    def __init__(self, enabled: bool) -> None:
        self.on = enabled

    def _c(self, code: str) -> str:
        return code if self.on else ""

    @property
    def dim(self) -> str:
        return self._c("\x1b[2m")

    @property
    def bold(self) -> str:
        return self._c("\x1b[1m")

    @property
    def reset(self) -> str:
        return self._c(RESET)

    @property
    def computed(self) -> str:
        return self._c("\x1b[36m")          # cyan: a program said this

    @property
    def model(self) -> str:
        return self._c("\x1b[35m")          # magenta: a model said this

    @property
    def ok(self) -> str:
        return self._c("\x1b[32m")

    @property
    def bad(self) -> str:
        return self._c("\x1b[31m")

    @property
    def warn(self) -> str:
        return self._c("\x1b[33m")

    @property
    def accent(self) -> str:
        return self._c("\x1b[34m")

    def paint(self, text: str, colour: str) -> str:
        return f"{colour}{text}{self.reset}" if self.on and colour else text


# -- glyphs ----------------------------------------------------------------
class Glyphs:
    COMPUTED = "◆"      # ◆
    MODEL = "◇"         # ◇
    PHASE = "▸"         # ▸
    OK = "✓"            # ✓
    BAD = "✗"           # ✗
    WARN = "!"
    DOT = "·"           # ·
    ARROW = "→"         # →


ASCII_GLYPHS = {"COMPUTED": "*", "MODEL": "o", "PHASE": ">", "OK": "+",
                "BAD": "x", "WARN": "!", "DOT": "-", "ARROW": "->"}

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
ASCII_SPINNER = "|/-\\"


def supports_unicode(stream) -> bool:
    enc = (getattr(stream, "encoding", "") or "").lower()
    return "utf" in enc


def colour_enabled(stream) -> bool:
    """TTY, and not explicitly disabled."""
    if os.environ.get("NO_COLOR"):
        return False
    mode = os.environ.get("HARNESS_UI", "").strip().lower()
    if mode in ("plain", "off", "none"):
        return False
    if mode in ("rich", "color", "colour", "on"):
        return True
    if os.environ.get("TERM", "") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


# -- live status -----------------------------------------------------------
class Status:
    """A single repainting line at the bottom, TTY only.

    It is erased before any permanent line is written, so a transcript never
    contains spinner frames.
    """

    def __init__(self, stream, theme: Theme, unicode_ok: bool = True) -> None:
        self.stream = stream
        self.theme = theme
        self.frames = SPINNER if unicode_ok else ASCII_SPINNER
        self.text = ""
        self.suffix_fn = None
        self._i = 0
        self._shown = False
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        return self.theme.on

    def set(self, text: str, suffix_fn=None) -> None:
        if not self.active:
            return
        with self._lock:
            self.text = text
            self.suffix_fn = suffix_fn
            self._paint()
            self._start()

    def clear(self) -> None:
        if not self.active:
            return
        with self._lock:
            if self._shown:
                self.stream.write("\r\x1b[2K")
                self.stream.flush()
                self._shown = False

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.3)
        self.clear()

    # -- internals ---------------------------------------------------------
    def _paint(self) -> None:
        if not self.text:
            return
        frame = self.frames[self._i % len(self.frames)]
        self._i += 1
        suffix = ""
        if self.suffix_fn:
            try:
                suffix = self.suffix_fn() or ""
            except Exception:
                suffix = ""
        t = self.theme
        line = (f"  {t.paint(frame, t.accent)} {self.text}"
                f"{t.dim}{suffix}{t.reset}")
        self.stream.write("\r\x1b[2K" + line)
        self.stream.flush()
        self._shown = True

    def _start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self) -> None:
        while not self._stop.wait(0.12):
            with self._lock:
                if self._shown and self.text:
                    self._paint()


# -- cost ------------------------------------------------------------------
# Rough public list prices, USD per million tokens, for the estimate printed
# at the end of a run. Deliberately coarse: it is a signal, not an invoice.
PRICES = {
    "opus": (15.0, 75.0), "sonnet": (3.0, 15.0), "haiku": (0.80, 4.0),
    "gpt-5": (1.25, 10.0), "gpt-4o": (2.5, 10.0), "mini": (0.15, 0.60),
    "nano": (0.05, 0.40), "o3": (2.0, 8.0), "pro": (1.25, 5.0),
    "flash": (0.30, 2.5), "70b": (0.60, 0.80), "8b": (0.05, 0.08),
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    low = (model or "").lower()
    for key, (pin, pout) in PRICES.items():
        if key in low:
            return (tokens_in * pin + tokens_out * pout) / 1_000_000
    return None


def human_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1000:.1f}k"
    return str(n)


def human_time(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m{seconds % 60:02d}s"
