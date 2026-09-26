"""Live capability probe (SPEC.md 3.3).

Two attempts before concluding a capability is absent: a single malformed
reply is not proof of incapability, and a false negative needlessly downgrades
a capable model to the text protocol.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .types import ProviderError

PROBE_TOOL = {
    "name": "echo",
    "description": "Echo the given text back.",
    "parameters": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}

PROBE_MESSAGES = [
    {"role": "system", "content": "You are a tool-using assistant."},
    {"role": "user", "content":
     'Call the echo tool with text "ping". Then reply with exactly this JSON '
     'and nothing else: {"ok": true, "n": 2}'},
]


@dataclass
class Capabilities:
    tool_calling: bool = False
    strict_json: bool = False
    probed: bool = False
    error: str = ""

    @property
    def text_protocol(self) -> bool:
        return not self.tool_calling


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def probe(gateway, model: str, cache_dir: Path | None = None,
          attempts: int = 2) -> Capabilities:
    """Never raises: probe failure means 'assume the safe path'."""
    cache_file = None
    if cache_dir:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", model)[:60]
        cache_file = Path(cache_dir) / f"capability-{safe}.json"
        if cache_file.is_file():
            try:
                d = json.loads(cache_file.read_text("utf-8"))
                return Capabilities(**d)
            except (OSError, json.JSONDecodeError, TypeError):
                pass

    caps = Capabilities()
    for attempt in range(attempts):
        # Vary the request per attempt so the call cache cannot serve the
        # retry -- the second attempt exists to tolerate a flaky reply.
        msgs = [dict(m) for m in PROBE_MESSAGES]
        if attempt:
            msgs[-1]["content"] += f" (attempt {attempt + 1})"
        try:
            reply = gateway.call(msgs, model, phase="P0",
                                 max_tokens=200, temperature=0.0,
                                 tools=[PROBE_TOOL], label="probe")
        except ProviderError as exc:
            caps.error = str(exc)[:120]
            continue

        caps.probed = True
        if reply.tool_calls and reply.tool_calls[0].name == "echo":
            caps.tool_calling = True
        parsed = _extract_json(reply.text)
        if isinstance(parsed, dict) and parsed.get("ok") is True:
            caps.strict_json = True
        if caps.tool_calling and caps.strict_json:
            break

    if cache_file:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(caps.__dict__), encoding="utf-8")
        except OSError:
            pass
    return caps
