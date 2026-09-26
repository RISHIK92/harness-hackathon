"""Failure classification and deterministic recovery dispatch (SPEC.md 27).

A failure is routed by its TYPE, not re-asked as a question.  Re-prompting is
what happens when the deterministic route is exhausted, not the first move.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PATCH_NOT_APPLIED = "PATCH_NOT_APPLIED"
SYNTAX_ERROR = "SYNTAX_ERROR"
TYPE_ERROR = "TYPE_ERROR"
LINT_NEW = "LINT_NEW"
TEST_FAILURE = "TEST_FAILURE"
COLLECTION_ERROR = "COLLECTION_ERROR"
COMMAND_TIMEOUT = "COMMAND_TIMEOUT"
DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"
PROVIDER_ERROR = "PROVIDER_ERROR"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
SCOPE_VIOLATION = "SCOPE_VIOLATION"
SIZE_VIOLATION = "SIZE_VIOLATION"
UNKNOWN = "UNKNOWN"

# type -> (deterministic first move, phase to re-enter if it does not resolve)
RECOVERY = {
    PATCH_NOT_APPLIED: ("re-read the file, then narrow to one hunk, then "
                        "de-escalate the format", "P3"),
    SYNTAX_ERROR: ("revert the hunk and re-prompt with the parser message", "P3"),
    TYPE_ERROR: ("show the call site and the signature, re-prompt that hunk", "P3"),
    LINT_NEW: ("apply the formatter to lines we touched, else re-prompt", "P3"),
    TEST_FAILURE: ("rerun once for flake, attribute to a hunk, re-prompt it", "P3"),
    COLLECTION_ERROR: ("revert: the suite cannot import, almost always ours",
                       "P3"),
    COMMAND_TIMEOUT: ("narrow the scope and retry once, then mark unverified",
                      "P4"),
    DEPENDENCY_ERROR: ("run the external probes; never install anything", "P1"),
    ENVIRONMENT_ERROR: ("run the external probes; the answer is often not code",
                        "P1"),
    PROVIDER_ERROR: ("back off, then fail over primary to cheap", "P4"),
    BUDGET_EXCEEDED: ("conservative mode on, finish the hunk, go to P5", "P5"),
    SCOPE_VIOLATION: ("revert the offending hunk", "P3"),
    SIZE_VIOLATION: ("re-scope with a tighter estimate", "P2"),
    UNKNOWN: ("re-prompt with the raw failure", "P3"),
}

PATTERNS = (
    (COLLECTION_ERROR, re.compile(
        r"(ERROR collecting|ImportError while|INTERNALERROR|"
        r"ModuleNotFoundError|cannot import name|conftest)", re.I)),
    (DEPENDENCY_ERROR, re.compile(
        r"(No module named|unresolved import|cannot find package|"
        r"unknown import path|is not in go.sum)", re.I)),
    (ENVIRONMENT_ERROR, re.compile(
        r"(KeyError: ['\"][A-Z_]{3,}|environment variable .* not set|"
        r"Missing required env)", re.I)),
    (TYPE_ERROR, re.compile(
        r"(TypeError|incompatible types|has no attribute|"
        r"cannot be assigned to|error TS\d+)", re.I)),
    (COMMAND_TIMEOUT, re.compile(r"timed out after", re.I)),
)


@dataclass
class Failure:
    kind: str
    detail: str
    phase: str = "P3"
    first_move: str = ""

    def render(self) -> str:
        return f"{self.kind}: {self.detail[:160]}"


def classify_edit_failure(exc) -> Failure:
    stage = getattr(exc, "stage", "")
    kind = {"parse": PATCH_NOT_APPLIED, "locate": PATCH_NOT_APPLIED,
            "apply": PATCH_NOT_APPLIED, "syntax": SYNTAX_ERROR,
            "scope": SCOPE_VIOLATION, "size": SIZE_VIOLATION}.get(stage, UNKNOWN)
    return _make(kind, getattr(exc, "detail", str(exc)))


def classify_text(text: str, default: str = UNKNOWN) -> Failure:
    for kind, pattern in PATTERNS:
        if pattern.search(text or ""):
            return _make(kind, text or "")
    return _make(default, text or "")


def classify_tests(classification, lint_blocked: bool = False) -> Failure:
    if lint_blocked:
        return _make(LINT_NEW, "new lint diagnostics on changed files")
    if getattr(classification, "collection_error", False):
        return _make(COLLECTION_ERROR, "the suite could not be collected")
    blocking = getattr(classification, "blocking", [])
    if blocking:
        return _make(TEST_FAILURE, ", ".join(blocking[:3]))
    return _make(UNKNOWN, "no blocking failure")


def _make(kind: str, detail: str) -> Failure:
    move, phase = RECOVERY.get(kind, RECOVERY[UNKNOWN])
    return Failure(kind=kind, detail=detail, phase=phase, first_move=move)
