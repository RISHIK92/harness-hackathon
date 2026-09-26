"""A scripted stand-in for the foundation model.

It inspects the prompt and returns a role-appropriate structured reply, so the
whole pipeline can be exercised offline and deterministically.  It is
deliberately literal-minded: it does not "understand" the issue, it pattern
matches, which keeps it honest about what the harness itself contributes.
"""
from __future__ import annotations

import json
import re


def _usage(prompt: str, out: str) -> dict:
    return {"prompt_tokens": max(1, len(prompt) // 4),
            "completion_tokens": max(1, len(out) // 4)}


def _reply(prompt: str, content: str, tool_calls=None,
           wire: str = "openai") -> dict:
    """Envelope in the shape the target wire actually returns."""
    u = _usage(prompt, content)
    if wire == "anthropic":
        blocks = [{"type": "text", "text": content}]
        for tc in tool_calls or []:
            blocks.append({"type": "tool_use", "id": tc["id"],
                           "name": tc["function"]["name"],
                           "input": json.loads(tc["function"]["arguments"])})
        return {"model": "mock-opus", "content": blocks,
                "stop_reason": "end_turn",
                "usage": {"input_tokens": u["prompt_tokens"],
                          "output_tokens": u["completion_tokens"]}}
    msg = {"content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"model": "mock-opus",
            "choices": [{"message": msg, "finish_reason": "stop"}],
            "usage": u}


def _first_path(prompt: str) -> str:
    """Anchor on the ranked suspects the harness supplied, as a model would.

    Falls back to any source path, then to any path at all.
    """
    ranked = re.findall(r"\b((?:[\w.-]+/)+[\w.-]+\.py):\d+\s+suspiciousness",
                        prompt)
    if ranked:
        return ranked[0]
    paths = re.findall(r"\b((?:[\w.-]+/)+[\w.-]+\.py)\b", prompt)
    for p in paths:
        if re.search(r"(^|/)tests?/|test_|_test\.|__init__\.py", p):
            continue
        return p
    return paths[0] if paths else "src/unknown.py"


def _first_line(prompt: str) -> int:
    m = re.search(r"\.py:(\d+)\s+suspiciousness", prompt)
    if m:
        return int(m.group(1))
    m = re.search(r"\.py:(\d+)", prompt)
    return int(m.group(1)) if m else 1


def _symbol(prompt: str) -> str:
    m = re.search(r"\((\w+)\)\s+suspiciousness", prompt)
    if m:
        return m.group(1).split(".")[-1]
    for rx in (r"\b([a-z_][a-z0-9_]{3,})\(\)", r"\bdef ([a-z_][a-z0-9_]{3,})"):
        m = re.search(rx, prompt)
        if m:
            return m.group(1)
    return "target"


def respond(body: dict, wire: str = "openai") -> dict:
    prompt = "\n".join(m.get("content", "") for m in body.get("messages", []))

    # capability probe
    if "Call the echo tool" in prompt:
        return _reply(prompt, '{"ok": true, "n": 2}', wire=wire,
                      tool_calls=[{"id": "c1", "function": {
                          "name": "echo",
                          "arguments": '{"text":"ping"}'}}])

    # P1 hypotheses
    if "Propose exactly" in prompt:
        n = int(re.search(r"Propose exactly (\d+)", prompt).group(1))
        sym = _symbol(prompt)
        pool = [
            {"id": "H1", "statement": f"{sym} does not guard its input",
             "predicts": "an unguarded index or attribute access exists",
             "check": {"kind": "grep", "arg": f"def {sym}"}},
            {"id": "H2", "statement": "a required environment variable is unset",
             "predicts": "the code reads an env var that is absent",
             "check": {"kind": "env", "arg": "MAILER_ENDPOINT"}},
            {"id": "H3", "statement": "a recent commit changed the boundary",
             "predicts": "the file changed in the last 90 days",
             "check": {"kind": "git", "arg": _first_path(prompt)}},
            {"id": "H4", "statement": "a pinned dependency is behind the floor",
             "predicts": "lockfile and manifest disagree",
             "check": {"kind": "version", "arg": "requests"}},
        ]
        return _reply(prompt, json.dumps({"hypotheses": pool[:n]}), wire=wire)

    # P1 synthesis
    if "ROOT CAUSE REPORT" in prompt:
        path, line = _first_path(prompt), _first_line(prompt)
        klass = "logic"
        if "is read by the code but is not set" in prompt:
            klass = "config"
        elif "pinned to" in prompt and "declares >=" in prompt:
            klass = "external"
        conf = "high" if "A failing test reproduces" in prompt else "medium"
        return _reply(prompt, json.dumps({
            "statement": f"{_symbol(prompt)} fails to handle the reported "
                         f"input at {path}:{line}",
            "classification": klass,
            "files": [{"path": path, "lines": [max(1, line - 2), line + 2],
                       "why": "highest suspiciousness"}],
            "confidence": conf,
            "external_factor": klass in ("config", "external"),
        }), wire=wire)

    # P2 scope
    if "Produce the change plan" in prompt:
        path, sym = _first_path(prompt), _symbol(prompt)
        return _reply(prompt, json.dumps({
            "fix_description": f"Guard the failing input path in {sym} so the "
                               f"reported case is handled instead of raising.",
            "files_to_change": [{"path": path, "symbol": sym,
                                 "intent": "add the missing guard"}],
            "files_must_not_change": ["tests/"],
            "interface_changes": False,
            "estimated_lines_changed": 4,
            "fix_classification": "minimal_edit",
        }), wire=wire)

    return _reply(prompt, '{"ok": true}', wire=wire)


def install(monkeypatch):
    """Point every adapter at the script."""
    from harness.model.adapters import anthropic as anth
    from harness.model.adapters import openai_compat as oai

    def fake(method, url, headers, body=None, timeout=60.0):
        if method == "GET" or url.endswith("/models"):
            return {"data": [{"id": "mock-opus", "context_length": 200000},
                             {"id": "mock-haiku", "context_length": 200000},
                             {"id": "text-embedding-mock"}]}
        wire = "anthropic" if url.endswith("/v1/messages") else "openai"
        return respond(body or {}, wire=wire)

    monkeypatch.setattr(oai, "request", fake)
    monkeypatch.setattr(anth, "request", fake)
    return fake
