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


# Every language the harness can localize, not just Python. A mock that
# only recognises `.py` invents a path on a JavaScript repository, and the
# resulting "file does not exist" looks like a harness bug when it is a
# limitation of the test double.
SRC_EXT = r"(?:py|js|mjs|cjs|jsx|ts|tsx|go|rb|java|rs|php)"
PATH_RX = rf"\b((?:[\w.-]+/)+[\w.-]+\.{SRC_EXT})\b"


def _first_path(prompt: str) -> str:
    """Anchor on the ranked suspects the harness supplied, as a model would.

    Falls back to any source path, then to any path at all.
    """
    ranked = re.findall(PATH_RX.rstrip(r"\b") + r":\d+\s+suspiciousness",
                        prompt)
    if ranked:
        return ranked[0]
    paths = re.findall(PATH_RX, prompt)
    for p in paths:
        if re.search(r"(^|/)tests?/|test_|_test\.|\.test\.|__init__\.py", p):
            continue
        return p
    return paths[0] if paths else "src/unknown.py"


def _first_line(prompt: str) -> int:
    m = re.search(rf"\.{SRC_EXT}:(\d+)\s+suspiciousness", prompt)
    if m:
        return int(m.group(1))
    m = re.search(rf"\.{SRC_EXT}:(\d+)", prompt)
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


# Correct fixes keyed by a distinctive substring of the code the harness shows
# the model. This simulates "the model produces a good patch" so the harness
# plumbing can be verified end to end.
FIXES = [
    ('parts = text.split("-")',
     '    parts = text.split("-")\n    if len(parts) != 3:\n'
     '        raise ValueError("expected YYYY-MM-DD, got %r" % text)',
     '    parts = text.split("-")'),
    ('return profile["name"].title()',
     '    profile = load_profile(store, user_id)\n    if profile is None:\n'
     '        return ""\n    return profile["name"].title()',
     '    profile = load_profile(store, user_id)\n'
     '    return profile["name"].title()'),
    ('name, qty = normalize_row(line)',
     '    for line in lines:\n        if not line.strip():\n'
     '            continue\n        name, qty = normalize_row(line)',
     '    for line in lines:\n        name, qty = normalize_row(line)'),
    ('return apply_discount(subtotal(items), percent)',
     '    if percent < 0:\n        raise ValueError("discount must not be '
     'negative")\n    return apply_discount(subtotal(items), percent)',
     '    return apply_discount(subtotal(items), percent)'),
    ('return ratio(part, whole) * 100',
     '    if whole == 0:\n        return 0.0\n'
     '    return ratio(part, whole) * 100',
     '    return ratio(part, whole) * 100'),
    ('return queue.pop(0)[1]',
     '    if not queue:\n        return None\n    return queue.pop(0)[1]',
     '    return queue.pop(0)[1]'),
    ('if qty > 100:',
     '    if qty >= 100:',
     '    if qty > 100:'),
    ('def haversine(lat1, lon1, lat2, lon2):',
     'def haversine(lat1, lon1, lat2, lon2, unit="km"):',
     'def haversine(lat1, lon1, lat2, lon2):'),
    ('return 2 * r * math.asin(math.sqrt(a))',
     '    km = 2 * r * math.asin(math.sqrt(a))\n'
     '    return km * 0.621371 if unit == "mi" else km',
     '    return 2 * r * math.asin(math.sqrt(a))'),
    ('slug = base + SEPARATOR + str(n)',
     '        slug = base + SEPARATOR + str(n)\n        n += 1',
     '        slug = base + SEPARATOR + str(n)'),
    # JavaScript. The evaluator's path is `make run ISSUE=<github url>`, and
    # until this entry existed no offline rehearsal of it could produce a
    # patch -- every scripted fix was Python, so the run always ended NO_FIX
    # for a reason that had nothing to do with the harness.
    ('return { total: projects.length, onTrack, atRisk, delayed };',
     '  const completed = projects.filter((p) => p.progress >= 90).length;\n'
     '  return { total: projects.length, onTrack, atRisk, delayed, '
     'completed };',
     '  return { total: projects.length, onTrack, atRisk, delayed };'),
]


def _numbered_lines(prompt: str) -> list:
    """The file the harness showed, with its line-number gutter removed."""
    m = re.search(r"THE CODE TO CHANGE:\n(.*?)\n\nCONSTRAINTS", prompt, re.S)
    if not m:
        return []
    return [re.sub(r"^\s*\d+\s*\|\s?", "", ln)
            for ln in m.group(1).splitlines()]


def _fix_for(prompt: str):
    for needle, replacement, search in FIXES:
        if needle in prompt:
            return search, replacement
    return None, None


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

    # P4 diff-sanity judge: name a real identifier from the diff, as the
    # anti-sycophancy rule requires.
    if "Does this diff address the stated root cause?" in prompt:
        added = [ln[1:].strip() for ln in prompt.splitlines()
                 if ln.startswith("+") and not ln.startswith("+++")]
        ident = ""
        for line in added:
            m = re.search(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b", line)
            if m:
                ident = m.group(1)
                break
        return _reply(prompt,
                      f"YES\nthe new {ident} check\nIt handles the reported "
                      f"input before the failing operation runs.", wire=wire)

    # P4 best-practices judge: flag only, never fix.
    if "Answer PASS or FLAG" in prompt:
        return _reply(prompt, "PASS", wire=wire)

    # P3 implementation
    if "OUTPUT FORMAT" in prompt:
        path = _first_path(prompt)
        search, replacement = _fix_for(prompt)
        if search is None:
            return _reply(prompt, "I cannot determine the fix.", wire=wire)
        if "<<<<<<< FILE" in prompt:
            # WHOLE_FILE: the harness shows the code; splice the fix into it.
            m = re.search(r"THE CODE TO CHANGE:\n(.*?)\n\nCONSTRAINTS",
                          prompt, re.S)
            body = m.group(1) if m else ""
            body = re.sub(r"^\s*\d+\s*\|\s?", "", body, flags=re.M)
            if search.strip() and search.strip() in body:
                body = body.replace(search, replacement, 1)
            return _reply(prompt, f"<<<<<<< FILE {path}\n{body}\n>>>>>>>",
                          wire=wire)
        if "REPLACE {0}:".format(path) in prompt or "<<<<<<< REPLACE" in prompt:
            m = re.search(r"REPLACE \S+?:(\d+)-(\d+)", prompt)
            if m:
                # Rebuild the requested span from the code the harness showed,
                # applying the fix inside it. Emitting a fixed string for any
                # span deletes whatever else the span covered -- a closing
                # brace, say -- which the harness rightly rejects as a syntax
                # error. A competent model edits the lines it was given.
                start, end = int(m.group(1)), int(m.group(2))
                body = _numbered_lines(prompt)
                if body:
                    # For LINE_RANGE the harness shows only the region, so
                    # absolute line numbers do not index into it.
                    span = (body if len(body) <= (end - start + 1)
                            else body[start - 1:end])
                    text = "\n".join(span)
                    if search.strip() and search.strip() in text:
                        text = text.replace(search, replacement, 1)
                    elif search.strip() and search.strip() in "\n".join(body):
                        text = None        # the fix is outside this span
                    if text is not None:
                        return _reply(prompt,
                                      f"<<<<<<< REPLACE {path}:{start}-{end}\n"
                                      f"{text}\n>>>>>>>", wire=wire)
                return _reply(prompt, f"<<<<<<< REPLACE {path}:{m.group(1)}-"
                                      f"{m.group(2)}\n{replacement}\n>>>>>>>",
                              wire=wire)
        return _reply(prompt,
                      f"<<<<<<< SEARCH {path}\n{search}\n=======\n"
                      f"{replacement}\n>>>>>>> REPLACE", wire=wire)

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
