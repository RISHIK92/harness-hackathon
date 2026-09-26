"""Human-readable phase logging to stdout (FR-5) with secret redaction (NFR-4).

ANSI is emitted only when stdout is a TTY, so piped output stays clean.
"""
from __future__ import annotations

import re
import sys
import time

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
    """Phase-prefixed stdout renderer."""

    def __init__(self, level: str = "info", secrets: list[str] | None = None,
                 stream=None) -> None:
        self.level = level
        self.secrets = [s for s in (secrets or []) if s]
        self.stream = stream or sys.stdout
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.started = time.time()
        self._phase = "--"

    # -- primitives --------------------------------------------------------
    def _write(self, text: str) -> None:
        self.stream.write(redact(text, self.secrets) + "\n")
        self.stream.flush()

    def raw(self, text: str = "") -> None:
        self._write(text)

    def rule(self, title: str = "") -> None:
        width = 64
        if title:
            head = f"── {title} "
            self._write(head + "─" * max(0, width - len(head)))
        else:
            self._write("─" * width)

    # -- phase logging -----------------------------------------------------
    def phase(self, phase: str) -> None:
        self._phase = phase
        name = PHASE_NAMES.get(phase, phase)
        self._write("")
        self._write(f"[{phase} {name}]".ljust(18) + "-" * 46)

    def line(self, msg: str, phase: str | None = None) -> None:
        p = phase or self._phase
        name = PHASE_NAMES.get(p, p)
        prefix = f"[{p} {name[:11]:<11}]"
        self._write(f"{prefix} {msg}")

    def cont(self, msg: str) -> None:
        """Continuation line, aligned under the prefix."""
        self._write(" " * 18 + msg)

    def debug(self, msg: str) -> None:
        if self.level == "debug":
            self.line(f"debug: {msg}")

    def warn(self, msg: str) -> None:
        self.line(f"! {msg}")

    def degraded(self, tag: str, detail: str = "") -> None:
        self.line(f"degraded: {tag}" + (f" ({detail})" if detail else ""))

    def note(self, tag: str, detail: str = "") -> None:
        self.line(f"note: {tag}" + (f" ({detail})" if detail else ""))

    def model_call(self, model: str, tin: int, tout: int, secs: float,
                   cached: int = 0, extra: str = "") -> None:
        bits = f"model={model} in={_k(tin)} out={_k(tout)}"
        if cached:
            bits += f" cache_read={_k(cached)}"
        bits += f" {secs:.1f}s"
        if extra:
            bits += f" {extra}"
        self.line(bits)

    def elapsed(self) -> float:
        return time.time() - self.started


def _k(n: int) -> str:
    if n >= 10_000:
        return f"{n/1000:.1f}k"
    return str(n)
