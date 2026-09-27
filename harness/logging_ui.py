"""Human-readable phase logging to stdout (FR-5) with secret redaction (NFR-4).

ANSI is emitted only when stdout is a TTY, so piped output stays clean.
"""
from __future__ import annotations

import re
import sys
import threading
import time

from .ui import (ASCII_GLYPHS, ASCII_SPINNER, Glyphs, SPINNER, Status,
                 Theme, colour_enabled, human_time, human_tokens,
                 supports_unicode)

# Anything shaped like a provider credential, longest prefixes first so the
# more specific pattern wins.
SECRET_PATTERNS = [
    re.compile(r"sk-ant-api\w{0,4}-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-or-v1-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bgsk_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bxai-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bcsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\btgp_v1_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bfw_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}"),
]

PHASE_NAMES = {
    "P0": "TRIAGE", "P1": "INVESTIGATE", "P2": "SCOPE",
    "P3": "IMPLEMENT", "P4": "VERIFY", "P5": "CONFIDENCE",
}


def redact(text: str, extra: list[str] | None = None) -> str:
    """Replace anything credential-shaped. Never raises."""
    if not text:
        return text
    out = str(text)
    for literal in extra or []:
        if literal and len(literal) >= 8:
            out = out.replace(literal, _mask(literal))
    for pat in SECRET_PATTERNS:
        out = pat.sub(lambda m: _mask(m.group(0)), out)
    return out


def _mask(secret: str) -> str:
    keep = min(11, max(4, len(secret) // 3))
    return secret[:keep] + "***"


class Logger:
    """Phase renderer.

    Rich on a TTY -- colour, glyphs and a live status line. Plain text
    everywhere else, so a piped transcript stays diffable and an evaluator's
    log file stays readable.
    """

    KEY_WIDTH = 16

    def __init__(self, level: str = "info", secrets: list[str] | None = None,
                 stream=None, rich: bool | None = None) -> None:
        self.level = level
        self.secrets = [s for s in (secrets or []) if s]
        self.stream = stream or sys.stdout
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.rich = colour_enabled(self.stream) if rich is None else rich
        self.theme = Theme(self.rich)
        self.unicode = supports_unicode(self.stream) or bool(rich)
        self.g = Glyphs if self.unicode else type("G", (), ASCII_GLYPHS)
        self.status = Status(self.stream, self.theme, self.unicode)
        self.started = time.time()
        self._phase = "--"
        self._counters = None          # set by the orchestrator
        self.screen = None             # set when the console owns the screen
        self._working_text = ""
        self._tick = 0
        self._ticker = None
        self._tick_stop = threading.Event()

    # -- live status -------------------------------------------------------
    def bind_counters(self, fn) -> None:
        """A callable returning the trailing `· 14.2k tokens · 0:12` text."""
        self._counters = fn

    def working(self, text: str) -> None:
        if self.screen is not None and self.screen.open_:
            # Painting once here froze the elapsed time and the token count
            # at the instant the phase started, so a four-minute phase read
            # "0s" throughout. The inline path has always had a thread for
            # this; the full-screen path needs one too.
            self._working_text = text
            self._paint_working()
            self._start_ticker()
            return
        self.status.set(text, self._counters)

    def _paint_working(self) -> None:
        screen = self.screen
        if screen is None or not screen.open_ or not self._working_text:
            return
        suffix = ""
        if self._counters:
            try:
                suffix = self._counters() or ""
            except Exception:
                suffix = ""
        t = self.theme
        frames = SPINNER if self.unicode else ASCII_SPINNER
        frame = frames[self._tick % len(frames)]
        self._tick += 1
        screen.set_status(f"  {t.paint(frame, t.accent)} "
                          f"{self._working_text}{t.dim}{suffix}{t.reset}")
        screen.render()

    def _start_ticker(self) -> None:
        if self._ticker and self._ticker.is_alive():
            return
        self._tick_stop.clear()

        def spin():
            while not self._tick_stop.wait(0.2):
                try:
                    self._paint_working()
                except Exception:
                    return          # the frame went away under us
        self._ticker = threading.Thread(target=spin, daemon=True)
        self._ticker.start()

    def _stop_ticker(self) -> None:
        self._tick_stop.set()
        ticker, self._ticker = self._ticker, None
        if ticker and ticker.is_alive():
            ticker.join(timeout=0.4)
        self._working_text = ""

    def done_working(self) -> None:
        self._stop_ticker()
        if self.screen is not None:
            self.screen.set_status("")
            if self.screen.open_:
                self.screen.render()
        self.status.clear()

    def close(self) -> None:
        self._stop_ticker()
        self.status.stop()

    # -- primitives --------------------------------------------------------
    def attach_screen(self, screen) -> None:
        """Send output into a full-screen frame instead of the stream."""
        self.screen = screen

    def detach_screen(self) -> str:
        screen, self.screen = self.screen, None
        return screen.transcript() if screen else ""

    def _write(self, text: str) -> None:
        clean = redact(text, self.secrets)
        if self.screen is not None and self.screen.open_:
            self.screen.append(clean)
            self.screen.render()
            return
        self.status.clear()
        self.stream.write(clean + "\n")
        self.stream.flush()

    def raw(self, text: str = "") -> None:
        self._write(text)

    def kv(self, key: str, value: str, colour: str = "",
           width: int | None = None) -> None:
        """An aligned key/value line. The plain form is byte-identical to the
        original layout, so captured output stays stable."""
        t = self.theme
        pad = key.ljust(width or self.KEY_WIDTH)
        indent = "  " if self.rich else ""
        self._write(f"{indent}{t.dim}{pad}{t.reset}"
                    f"{t.paint(str(value), colour)}")

    def step(self, kind: str, label: str, value: str = "",
             colour: str = "", plain: str | None = None) -> None:
        """One evidence line. `kind` is computed | model | ok | bad | warn.

        `plain` is the exact text for non-TTY output. Presentation changes
        must not alter a captured transcript.
        """
        t = self.theme
        glyph, gcolour = {
            "computed": (self.g.COMPUTED, t.computed),
            "model": (self.g.MODEL, t.model),
            "ok": (self.g.OK, t.ok),
            "bad": (self.g.BAD, t.bad),
            "warn": (self.g.WARN, t.warn),
        }.get(kind, (self.g.DOT, ""))
        if not self.rich:
            # plain mode keeps the original wording so transcripts and log
            # greps continue to read the same way
            self.line(plain if plain is not None
                      else f"{label}" + (f" {value}" if value else ""))
            return
        head = f"  {t.paint(glyph, gcolour)} {label.ljust(14)}"
        self._write(head + t.paint(str(value), colour))

    def banner(self, version: str = "2.1") -> None:
        t = self.theme
        if not self.rich:
            self.rule(f"HARNESS v{version}")
            return
        bar = "\u2500" * 62 if self.unicode else "-" * 62
        tl, tr = ("\u256d", "\u256e") if self.unicode else ("+", "+")
        bl, br = ("\u2570", "\u256f") if self.unicode else ("+", "+")
        v = "\u2502" if self.unicode else "|"
        mark = "\u25c8" if self.unicode else "#"
        title = f"{t.bold}{mark} HARNESS{t.reset}"
        pad = " " * (62 - len(" # HARNESS") - len(f"v{version}") - 2)
        self._write("")
        self._write(f"  {t.accent}{tl}{bar}{tr}{t.reset}")
        self._write(f"  {t.accent}{v}{t.reset} {title}{pad}"
                    f"{t.dim}v{version}{t.reset} {t.accent}{v}{t.reset}")
        self._write(f"  {t.accent}{bl}{bar}{br}{t.reset}")

    def legend(self) -> None:
        if not self.rich:
            return
        t = self.theme
        self._write(f"  {t.dim}{t.reset}{t.paint(self.g.COMPUTED, t.computed)}"
                    f"{t.dim} computed   {t.reset}"
                    f"{t.paint(self.g.MODEL, t.model)}{t.dim} model"
                    f"        every claim shows where it came from{t.reset}")

    def rule(self, title: str = "") -> None:
        width = 64
        if title:
            head = f"── {title} "
            self._write(head + "─" * max(0, width - len(head)))
        else:
            self._write("─" * width)

    # -- phase logging -----------------------------------------------------
    def set_phase(self, phase: str) -> None:
        """Set the prefix without emitting a banner."""
        self._phase = phase

    def phase(self, phase: str, note: str = "") -> None:
        self._phase = phase
        name = PHASE_NAMES.get(phase, phase)
        self._write("")
        if not self.rich:
            self._write(f"[{phase} {name}]".ljust(18) + "-" * 46)
            return
        t = self.theme
        head = (f"  {t.paint(self.g.PHASE, t.accent)} "
                f"{t.bold}{name}{t.reset}")
        tail = f"{t.dim}{note}{t.reset}" if note else ""
        gap = max(1, 58 - len(name) - len(note))
        self._write(head + " " * gap + tail)

    def line(self, msg: str, phase: str | None = None) -> None:
        if self.rich:
            t = self.theme
            self._write(f"  {t.dim}{self.g.DOT}{t.reset} {msg}")
            return
        p = phase or self._phase
        name = PHASE_NAMES.get(p, p)
        prefix = f"[{p} {name[:11]:<11}]"
        self._write(f"{prefix} {msg}")

    def cont(self, msg: str) -> None:
        """Continuation, aligned under the value column."""
        if self.rich:
            self._write("    " + self.theme.dim + msg + self.theme.reset)
        else:
            self._write(" " * 18 + msg)

    def debug(self, msg: str) -> None:
        if self.level == "debug":
            self.line(f"debug: {msg}")

    def warn(self, msg: str) -> None:
        if self.rich:
            self.step("warn", "warning", msg, self.theme.warn)
        else:
            self.line(f"! {msg}")

    def ok(self, label: str, value: str = "", plain: str | None = None
             ) -> None:
        self.step("ok", label, value,
                  {"ok": self.theme.ok, "bad": self.theme.bad}.get("ok", ""),
                  plain)

    def fail(self, label: str, value: str = "", plain: str | None = None
             ) -> None:
        self.step("bad", label, value,
                  {"ok": self.theme.ok, "bad": self.theme.bad}.get("bad", ""),
                  plain)

    def computed(self, label: str, value: str = "", plain: str | None = None
             ) -> None:
        self.step("computed", label, value,
                  {"ok": self.theme.ok, "bad": self.theme.bad}.get("computed", ""),
                  plain)

    def degraded(self, tag: str, detail: str = "") -> None:
        if self.rich:
            self.step("warn", "degraded",
                      tag + (f"  {detail}" if detail else ""),
                      self.theme.warn)
        else:
            self.line(f"degraded: {tag}" + (f" ({detail})" if detail else ""))

    def note(self, tag: str, detail: str = "") -> None:
        if self.rich:
            self.step("computed", "note",
                      tag + (f"  {detail}" if detail else ""), self.theme.dim)
        else:
            self.line(f"note: {tag}" + (f" ({detail})" if detail else ""))

    def model_call(self, model: str, tin: int, tout: int, secs: float,
                   cached: int = 0, extra: str = "",
                   from_cache: bool = False) -> None:
        if from_cache and not self.rich:
            extra = "[cached]"
        if not self.rich:
            bits = f"model={model} in={_k(tin)} out={_k(tout)}"
            if cached:
                bits += f" cache_read={_k(cached)}"
            bits += f" {secs:.1f}s"
            if extra:
                bits += f" {extra}"
            self.line(bits)
            return
        t = self.theme
        detail = (f"{model}  {human_tokens(tin)}{self.g.ARROW}"
                  f"{human_tokens(tout)}"
                  + (f"  cache {human_tokens(cached)}" if cached else "")
                  + (f"  {secs:.1f}s" if not from_cache else "  replayed"))
        self.step("model", extra or "call", detail, t.dim)

    def elapsed(self) -> float:
        return time.time() - self.started


def _k(n: int) -> str:
    if n >= 10_000:
        return f"{n/1000:.1f}k"
    return str(n)
