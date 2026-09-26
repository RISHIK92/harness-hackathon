"""Test-result parsing (SPEC.md 9.4).

Machine-readable first, regex last.  Stable test ids are what baseline
diffing rests on (FR-29), so this cannot be hand-waved: a low-confidence
parse downgrades classification and promotes the diff-sanity judge to
blocking.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

PASS, FAIL, ERROR, SKIP = "pass", "fail", "error", "skip"
FAILING = (FAIL, ERROR)
PASSING = (PASS,)


@dataclass
class TestResults:
    tests: dict = field(default_factory=dict)        # id -> status
    failures: dict = field(default_factory=dict)     # id -> failure text
    confidence: str = "high"                         # high | low
    parser: str = ""
    total: int = 0
    collection_error: bool = False
    raw_tail: str = ""

    @property
    def failing(self) -> list[str]:
        return sorted(t for t, s in self.tests.items() if s in FAILING)

    @property
    def passing(self) -> list[str]:
        return sorted(t for t, s in self.tests.items() if s in PASSING)

    def counts(self) -> dict:
        out = {PASS: 0, FAIL: 0, ERROR: 0, SKIP: 0}
        for s in self.tests.values():
            out[s] = out.get(s, 0) + 1
        return out


# ---------------------------------------------------------------- junit xml
def parse_junit(path: Path) -> TestResults:
    res = TestResults(parser="junit-xml")
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        return TestResults(confidence="low", parser="junit-xml",
                           raw_tail=f"unparseable junit: {exc}")

    for tc in root.iter("testcase"):
        tid = test_id(tc.get("classname") or "", tc.get("name") or "",
                      tc.get("file"))
        status = PASS
        detail = ""
        for child in tc:
            tag = child.tag.lower()
            if tag == "failure":
                status = FAIL
            elif tag == "error":
                status = ERROR
            elif tag == "skipped":
                status = SKIP
            else:
                continue
            detail = (child.get("message") or "") + "\n" + (child.text or "")
        res.tests[tid] = status
        if status in FAILING:
            res.failures[tid] = detail.strip()[:4000]

    res.total = len(res.tests)
    if res.total == 0:
        res.confidence = "low"
    return res


def test_id(classname: str, name: str, file: str | None = None) -> str:
    """Canonical, stable across runs."""
    if file:
        return f"{file}::{name}"
    if classname:
        return f"{classname}::{name}"
    return name


# ---------------------------------------------------------------- go / cargo
def parse_go_json(text: str) -> TestResults:
    res = TestResults(parser="go-json")
    status_map = {"pass": PASS, "fail": FAIL, "skip": SKIP}
    output: dict[str, list[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = rec.get("Test")
        if not name:
            continue
        tid = f"{rec.get('Package','')}::{name}"
        action = rec.get("Action")
        if action in status_map:
            res.tests[tid] = status_map[action]
            if status_map[action] in FAILING:
                res.failures[tid] = "".join(output.get(tid, []))[-4000:]
        elif action == "output":
            output.setdefault(tid, []).append(rec.get("Output", ""))
    res.total = len(res.tests)
    if not res.total:
        res.confidence = "low"
    return res


def parse_jest_json(text: str) -> TestResults:
    res = TestResults(parser="jest-json")
    try:
        data = json.loads(text[text.index("{"):])
    except (ValueError, json.JSONDecodeError):
        return TestResults(confidence="low", parser="jest-json")
    for suite in data.get("testResults", []):
        f = suite.get("name", "")
        for tc in suite.get("assertionResults", []):
            tid = f"{f}::{tc.get('fullName') or tc.get('title')}"
            st = {"passed": PASS, "failed": FAIL,
                  "pending": SKIP, "skipped": SKIP}.get(tc.get("status"), PASS)
            res.tests[tid] = st
            if st in FAILING:
                res.failures[tid] = "\n".join(
                    tc.get("failureMessages", []))[:4000]
    res.total = len(res.tests)
    if not res.total:
        res.confidence = "low"
    return res


# ---------------------------------------------------------------- fallback
PYTEST_LINE = re.compile(r"^(FAILED|ERROR|PASSED)\s+(\S+)", re.M)
PYTEST_TAIL = re.compile(
    r"(\d+) failed[,\s]|(\d+) passed|(\d+) error", re.I)
COLLECT_ERR = re.compile(
    r"(ERROR collecting|ImportError while|INTERNALERROR|SyntaxError|"
    r"ModuleNotFoundError|cannot import name)", re.I)


def parse_text(text: str, framework: str = "") -> TestResults:
    """Regex fallback. Always low confidence -- ids may not be stable."""
    res = TestResults(parser=f"regex:{framework or 'generic'}",
                      confidence="low", raw_tail=text[-4000:])
    for m in PYTEST_LINE.finditer(text):
        verdict, nodeid = m.group(1), m.group(2).rstrip(":")
        res.tests[nodeid] = {"FAILED": FAIL, "ERROR": ERROR,
                             "PASSED": PASS}[verdict]
    res.total = len(res.tests)
    if COLLECT_ERR.search(text):
        res.collection_error = True
    return res


def parse(result, toolchain, junit_path: Path | None = None) -> TestResults:
    """Pick the best available parser for a runner Result."""
    combined = (result.stdout or "") + "\n" + (result.stderr or "")

    if junit_path and Path(junit_path).is_file():
        res = parse_junit(Path(junit_path))
        if res.total:
            if COLLECT_ERR.search(combined):
                res.collection_error = True
            res.raw_tail = combined[-4000:]
            return res

    fw = (toolchain.framework or "").lower() if toolchain else ""
    if fw == "gotest" or '"Action"' in combined:
        res = parse_go_json(combined)
        if res.total:
            return res
    if fw in ("jest", "vitest") and '"testResults"' in combined:
        res = parse_jest_json(combined)
        if res.total:
            return res

    res = parse_text(combined, fw)
    # A zero-test parse on a non-zero exit is a collection failure.
    if not res.total and result.exit_code != 0:
        res.collection_error = True
    return res
