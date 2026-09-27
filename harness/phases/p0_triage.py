"""Phase 0: deterministic anchor extraction and task typing (SPEC.md 7.1, 22).

Everything here is regex and set arithmetic.  One cheap model call normalizes
the issue, and it is skipped entirely when the deterministic pass already
filled the fields (NFR-1).
"""
from __future__ import annotations

import re
from pathlib import Path

from ..records import Anchors, IssueRecord

SRC_EXT = ("py", "js", "jsx", "ts", "tsx", "go", "rs", "java", "rb", "php",
           "c", "h", "cpp", "cc", "cs", "kt", "swift", "scala", "sh")

PATH_RE = re.compile(r"\b((?:[\w.-]+/)*[\w.-]+\.(?:" + "|".join(SRC_EXT) + r"))\b")
BACKTICK = re.compile(r"`([^`\n]{1,80})`")
CALL_RE = re.compile(r"\b([a-z_][a-z0-9_]{2,})\s*\(")
DOTTED = re.compile(r"\b([A-Z][A-Za-z0-9_]+)\.([a-z_][A-Za-z0-9_]+)\b")
CAMEL_RE = re.compile(r"\b([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)\b")
ERROR_RE = re.compile(
    r"^\s*(?:[\w.]*(?:Error|Exception|Warning|Fault)\b[^\n]{0,160})", re.M)
QUOTED = re.compile(r'"([^"\n]{6,120})"')
PY_FRAME = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')
JS_FRAME = re.compile(r"at\s+([\w.<>$]+)\s+\(([^:)]+):(\d+):\d+\)")
GO_FRAME = re.compile(r"^\s+([\w./-]+\.go):(\d+)", re.M)
VERSION_RE = re.compile(r"\b(?:v?\d+\.\d+(?:\.\d+)?(?:[-+][\w.]+)?)\b")
PKG_AT = re.compile(r"\b([\w.@/-]+)@(\d+\.\d+[\w.-]*)\b")
FENCE = re.compile(r"```[\w]*\n(.*?)```", re.S)
CMD_LINE = re.compile(r"^\s*(?:\$|>>>|#)\s*(\S.*)$", re.M)

EXPECTED_RE = re.compile(
    r"(?:expected|should(?:\s+be)?|ought to)\s*[:\-]?\s*(.{5,160})", re.I)
ACTUAL_RE = re.compile(
    r"(?:actual(?:ly)?|but (?:it |we )?(?:got|get|see)|instead(?:\s+of)?|"
    r"got)\s*[:\-]?\s*(.{5,160})", re.I)

REGRESSION_RE = re.compile(
    r"\b(used to work|worked before|regression|since (?:version|v?\d)|"
    r"after (?:upgrading|updating|the upgrade)|stopped working|"
    r"broke(?:n)? (?:in|after)|no longer works|recently)\b", re.I)

# Word-PREFIX matching: a trailing \b would miss "crashes", and a leading \b
# would miss the "Error" inside "IndexError".
TYPE_PATTERNS = (
    ("FEATURE", re.compile(
        r"\b(add support|feature request|should (?:also )?(?:support|accept|"
        r"allow)|implement\w*|introduce\w*|new (?:option|flag|parameter|"
        r"argument)|would be (?:nice|good|great) (?:to|if)|please add)", re.I)),
    ("REFACTOR", re.compile(
        r"\b(refactor\w*|clean\s?up|simplif\w+|renam\w+|extract\w*|"
        r"deduplicat\w+|tidy|restructur\w+|move .* into)", re.I)),
    ("DEPENDENCY", re.compile(
        r"\b(dependenc\w+|upgrad\w+|version conflict|lock ?file|"
        r"requirements\.txt|package\.json|pinned|incompatible version)", re.I)),
    ("CONFIG", re.compile(
        r"\b(environment variable|env var|config\w*|\.env\b|settings|"
        r"not set|missing setting|misconfigur\w*)", re.I)),
    ("BUG_FIX", re.compile(
        r"(?:\b|(?<=[a-z]))(crash\w*|[Ee]rror\w*|[Ee]xception\w*|traceback|"
        r"fail\w*|broken|bug\b|incorrect\w*|wrong\b|unexpected\w*|"
        r"regress\w+|raise[sd]?\b|hang\w*|stuck\b|infinite loop)", re.I)),
)

# An UPPER_SNAKE name inside a KeyError is an environment-variable signal.
ENV_KEY_RE = re.compile(r"KeyError:?\s*['\"]?([A-Z][A-Z0-9_]{3,})")


def _resolve_path(cand: str, known_files: set[str]) -> list:
    """A path from prose down to a path in this repository.

    Issues routinely link to code rather than quote it, and a GitHub blob URL
    carries the path with `github.com/owner/repo/blob/<sha>/` in front of it.
    Matching only the whole candidate missed every such reference -- on a real
    issue that named its three fix sites by URL, the harness found none of
    them.

    So leading segments are stripped one at a time until the tail names
    something real. Beyond the first suffix match the tail must be *unique*
    in the repository: "utils.ts" appearing forty times is not a reference to
    any one of them.
    """
    if cand in known_files:
        return [cand]
    exact = [f for f in known_files if f.endswith("/" + cand)]
    if exact:
        return exact[:2]

    parts = cand.split("/")
    for i in range(1, len(parts)):
        tail = "/".join(parts[i:])
        if tail in known_files:
            return [tail]
        matches = [f for f in known_files if f.endswith("/" + tail)]
        if len(matches) == 1:
            return matches
        if len(matches) > 1:
            break          # ambiguous: naming it would be a guess
    return []


def extract_anchors(text: str, known_files: set[str],
                    known_symbols: set[str] | None = None) -> Anchors:
    a = Anchors()
    known_symbols = known_symbols or set()

    # paths, cross-checked against the repository
    seen_files = []
    for m in PATH_RE.finditer(text):
        seen_files.extend(_resolve_path(m.group(1).lstrip("./"), known_files))
    a.files = _uniq(seen_files)

    # frames
    frames = [(f.lstrip("./"), int(n), fn) for f, n, fn in PY_FRAME.findall(text)]
    frames += [(f.lstrip("./"), int(n), fn) for fn, f, n in JS_FRAME.findall(text)]
    frames += [(f, int(n), "") for f, n in GO_FRAME.findall(text)]
    a.frames = _uniq(frames)
    for path, _line, _fn in a.frames:
        base = path.split("/")[-1]
        for known in known_files:
            if known.endswith("/" + base) or known == base or known == path:
                a.files.append(known)
    a.files = _uniq(a.files)

    # symbols
    syms = [m.group(1) for m in CALL_RE.finditer(text)]
    syms += [b for b in BACKTICK.findall(text)
             if re.fullmatch(r"[A-Za-z_][\w.]*", b)]
    syms += [f"{c}.{m}" for c, m in DOTTED.findall(text)]
    syms += CAMEL_RE.findall(text)
    syms += [fn for _f, _l, fn in a.frames if fn and fn != "<module>"]
    if known_symbols:
        ranked = [s for s in syms if s.split(".")[-1] in known_symbols]
        a.symbols = _uniq(ranked) or _uniq(syms)[:8]
    else:
        a.symbols = _uniq(syms)[:12]

    # errors, versions, repro
    a.errors = _uniq([e.strip() for e in ERROR_RE.findall(text)]
                     + [q for q in QUOTED.findall(text)])[:8]
    a.versions = _uniq(VERSION_RE.findall(text)
                       + [f"{p}@{v}" for p, v in PKG_AT.findall(text)])[:8]
    repro = [b.strip() for b in FENCE.findall(text)]
    repro += [c.strip() for c in CMD_LINE.findall(text)]
    a.repro = _uniq(repro)[:5]
    return a


def _uniq(items):
    out, seen = [], set()
    for x in items:
        key = str(x)
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def vagueness(a: Anchors, text: str) -> float:
    """1 - weighted coverage. >= 0.5 routes to hypothesis mode (FR-15)."""
    weights = {"frames": 0.30, "errors": 0.20, "files": 0.20,
               "symbols": 0.15, "repro": 0.15}
    score = sum(w for name, w in weights.items() if getattr(a, name))
    if len(text.split()) < 15:
        score -= 0.15
    return round(max(0.0, min(1.0, 1.0 - score)), 2)


def classify_task(text: str, anchors: Anchors) -> str:
    """SPEC.md 22. UNKNOWN falls back to BUG_FIX + conservative mode."""
    # A KeyError naming an UPPER_SNAKE key is a configuration problem, and
    # saying so early keeps the pipeline from hunting for a code bug.
    if ENV_KEY_RE.search(text):
        return "CONFIG"

    for name, pattern in TYPE_PATTERNS:
        if pattern.search(text):
            if name == "CONFIG" and (anchors.frames or anchors.errors):
                if not re.search(r"KeyError|not set|missing", text, re.I):
                    continue
            return name

    # Regression language alone is enough: something used to work.
    if is_regression(text):
        return "BUG_FIX"
    # An extracted exception name is a bug signal even without bug words.
    if anchors.errors and any(
            e.strip().split(":")[0].endswith(("Error", "Exception"))
            for e in anchors.errors):
        return "BUG_FIX"
    return "UNKNOWN"


def effective_type(task_type: str) -> tuple[str, bool]:
    """(pipeline type, conservative?).

    UNKNOWN runs the bug-fix pipeline with conservative mode on, so a
    misclassification costs a tighter diff rather than the wrong pipeline.
    """
    if task_type == "UNKNOWN":
        return "BUG_FIX", True
    return task_type, False


def is_regression(text: str) -> bool:
    return bool(REGRESSION_RE.search(text))


def first_sentence(text: str, limit: int = 140) -> str:
    cleaned = " ".join(text.strip().split())
    m = re.match(r"(.{10,%d}?[.!?])(\s|$)" % limit, cleaned)
    return (m.group(1) if m else cleaned[:limit]).strip()


def triage(issue_text: str, known_files: set[str],
           known_symbols: set[str] | None = None,
           forced_type: str | None = None) -> IssueRecord:
    a = extract_anchors(issue_text, known_files, known_symbols)
    v = vagueness(a, issue_text)
    rec = IssueRecord(
        raw=issue_text,
        title=first_sentence(issue_text),
        symptom=first_sentence(issue_text),
        anchors=a,
        vagueness=v,
        min_hypotheses=3 if v >= 0.5 else 2,
        task_type=forced_type or classify_task(issue_text, a),
    )
    m = EXPECTED_RE.search(issue_text)
    rec.expected = m.group(1).strip() if m else None
    m = ACTUAL_RE.search(issue_text)
    rec.actual = m.group(1).strip() if m else None
    return rec


def needs_normalization(rec: IssueRecord) -> bool:
    """Skip the cheap model call when extraction already filled the fields."""
    return not (rec.title and (rec.expected or rec.actual))
