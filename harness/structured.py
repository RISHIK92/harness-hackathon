"""Structured model output with repair (SPEC.md invariant 3).

Every model output the harness acts on is schema-validated; a validation
failure is a typed, repairable event.  Never `try: json.loads(...) except: pass`.

Two protocols are supported so a model without tool calling or strict JSON
still runs: labelled fields (most robust) and fenced JSON.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

FENCE = re.compile(r"```(?:json|JSON)?\s*\n(.*?)```", re.S)
OBJECT = re.compile(r"\{.*\}", re.S)


class ParseFailure(Exception):
    def __init__(self, detail: str, raw: str = "") -> None:
        super().__init__(detail)
        self.detail = detail
        self.raw = raw[:2000]


@dataclass
class Field:
    name: str
    required: bool = True
    question: str = ""          # the narrower follow-up when it is missing


def extract_json(text: str) -> dict:
    """Fenced block, then bare object, then failure. Tolerant of prose."""
    if not text or not text.strip():
        raise ParseFailure("empty reply")
    for candidate in _json_candidates(text):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            repaired = _repair(candidate)
            if repaired is None:
                continue
            value = repaired
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            return {"items": value}
    raise ParseFailure("no JSON object found", text)


def _json_candidates(text: str):
    for m in FENCE.finditer(text):
        yield m.group(1).strip()
    m = OBJECT.search(text)
    if m:
        yield m.group(0)
    yield text.strip()


def _repair(raw: str):
    """Fix the three malformations models actually produce."""
    attempts = [
        raw,
        re.sub(r",\s*([}\]])", r"\1", raw),          # trailing commas
        raw.replace("'", '"'),                        # single quotes
        re.sub(r"//[^\n]*", "", raw),                 # line comments
    ]
    # unterminated object: close it
    opens = raw.count("{") - raw.count("}")
    if opens > 0:
        attempts.append(raw + "}" * opens)
    for attempt in attempts:
        try:
            return json.loads(attempt)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


LABEL = re.compile(r"^\s*([A-Z][A-Z_ ]{2,30}?)\s*:\s*(.*)$")


def extract_labelled(text: str, fields: list[str]) -> dict:
    """Parse `FIELD: value` blocks, the most robust protocol for weak models."""
    wanted = {f.upper() for f in fields}
    out: dict[str, str] = {}
    current = None
    for line in (text or "").splitlines():
        m = LABEL.match(line)
        if m and m.group(1).replace(" ", "_").upper() in wanted:
            current = m.group(1).replace(" ", "_").upper()
            out[current] = m.group(2).strip()
        elif current and line.strip():
            out[current] = (out[current] + " " + line.strip()).strip()
        elif current and not line.strip():
            current = None
    return {k: v for k, v in out.items() if v}


def choose_index(text: str, n: int) -> int | None:
    """Parse a selection answer. T0's whole protocol is picking a number."""
    if not text:
        return None
    if re.search(r"\bNONE\b", text, re.I):
        return -1
    m = re.search(r"\b(\d{1,3})\b", text)
    if not m:
        return None
    idx = int(m.group(1))
    if 1 <= idx <= n:
        return idx - 1
    return None


def ask_structured(router, role: str, phase: str, messages: list,
                   fields: list[str], log, cache_prefix: int = 0,
                   max_tokens: int = 2000, attempts: int = 2) -> dict:
    """Call, parse, and on failure re-ask a NARROWER question -- not a vaguer one."""
    last_raw = ""
    for attempt in range(attempts):
        reply = router.call(role, messages, phase, max_tokens=max_tokens,
                            cache_prefix=cache_prefix)
        last_raw = reply.text
        try:
            data = extract_json(reply.text)
            missing = [f for f in fields if f not in data]
            if not missing:
                return data
            labelled = extract_labelled(reply.text, missing)
            for k, v in labelled.items():
                data.setdefault(k.lower(), v)
            if not [f for f in fields if f not in data]:
                return data
            detail = f"missing field(s): {', '.join(f for f in fields if f not in data)}"
        except ParseFailure as exc:
            labelled = extract_labelled(reply.text, fields)
            if labelled and not [f for f in fields
                                 if f.upper() not in labelled]:
                return {k.lower(): v for k, v in labelled.items()}
            detail = exc.detail

        log.debug(f"{role}: {detail}; re-asking narrowly")
        messages = messages + [
            {"role": "assistant", "content": reply.text[:1500]},
            {"role": "user", "content":
             f"That reply could not be parsed ({detail}). Reply with ONLY a "
             f"JSON object containing exactly these keys: "
             f"{', '.join(fields)}. No prose, no code fence."},
        ]
    raise ParseFailure(f"could not parse a structured reply after {attempts} "
                       f"attempts", last_raw)
