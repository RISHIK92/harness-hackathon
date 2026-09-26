"""External-factor probes (FR-14, SPEC.md 4.6).

Executed checks, not prompt instructions.  A model asked to "check external
factors" answers EXTERNAL_FACTOR: no without checking anything, so the
harness runs the probes and the model only interprets the results.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..verify.runner import run
from .history import manifest_changes


@dataclass
class Finding:
    kind: str            # dependency | env | runtime | contract | config
    detail: str
    severity: str = "medium"

    def render(self) -> str:
        return f"[{self.kind}] {self.detail}"


@dataclass
class ExternalFactors:
    checked: list = field(default_factory=list)
    findings: list = field(default_factory=list)

    @property
    def ruled_out(self) -> bool:
        return not self.findings

    def render(self) -> str:
        if not self.checked:
            return "external factors: not checked"
        if self.ruled_out:
            return (f"external factors: {len(self.checked)} probes run, "
                    "none implicated")
        return ("external factors:\n"
                + "\n".join("  " + f.render() for f in self.findings))

    def to_json(self) -> dict:
        return {"checked": self.checked,
                "findings": [f.__dict__ for f in self.findings],
                "ruled_out": self.ruled_out}


REQ_PIN = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*([=<>!~]=+)\s*([0-9][\w.]*)",
                     re.M)
ENV_USE = re.compile(
    r"""(?:os\.environ\[|os\.environ\.get\(|os\.getenv\(|process\.env\.|"""
    r"""std::env::var\(|ENV\[)\s*["']?([A-Z][A-Z0-9_]{2,})""")


def probe_all(repo: Path, toolchain=None, search=None) -> ExternalFactors:
    ef = ExternalFactors()
    for fn in (_probe_env_vars, _probe_versions, _probe_runtime,
               _probe_manifest_churn, _probe_missing_config):
        try:
            fn(repo, ef, toolchain, search)
        except Exception:                 # a probe must never end the run
            continue
    return ef


def _probe_env_vars(repo: Path, ef: ExternalFactors, toolchain, search) -> None:
    ef.checked.append("environment variables referenced by the code")
    names: set[str] = set()
    for path in _source_files(repo, limit=400):
        try:
            text = path.read_text("utf-8", errors="replace")
        except OSError:
            continue
        names.update(ENV_USE.findall(text))
    missing = sorted(n for n in names
                     if n not in os.environ and not _looks_optional(n))
    for name in missing[:8]:
        ef.findings.append(Finding(
            "env", f"{name} is read by the code but is not set in this "
                   "environment", "high"))


def _looks_optional(name: str) -> bool:
    return name in {"PATH", "HOME", "PWD", "CI", "DEBUG", "TZ", "LANG",
                    "VIRTUAL_ENV", "NO_COLOR", "PYTHONPATH", "TERM", "USER"}


def _probe_versions(repo: Path, ef: ExternalFactors, toolchain, search) -> None:
    """Declared vs pinned: a lockfile behind the declared floor is a skew."""
    ef.checked.append("declared vs pinned dependency versions")
    declared: dict[str, str] = {}
    for name in ("pyproject.toml", "setup.py", "setup.cfg"):
        f = repo / name
        if f.is_file():
            body = f.read_text("utf-8", errors="replace")
            for m in re.finditer(r'["\']([A-Za-z0-9._-]+)\s*>=\s*([0-9][\w.]*)',
                                 body):
                declared[m.group(1).lower()] = m.group(2)

    pinned: dict[str, str] = {}
    for name in ("requirements.lock", "requirements.txt", "poetry.lock"):
        f = repo / name
        if f.is_file():
            for m in REQ_PIN.finditer(f.read_text("utf-8", errors="replace")):
                if m.group(2).startswith("=="):
                    pinned[m.group(1).lower()] = m.group(3)

    for pkg, floor in declared.items():
        got = pinned.get(pkg)
        if got and _version_lt(got, floor):
            ef.findings.append(Finding(
                "dependency",
                f"{pkg} is pinned to {got} but the project declares >={floor}",
                "high"))


def _version_lt(a: str, b: str) -> bool:
    def parts(v):
        return [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", v)[:4]]
    pa, pb = parts(a), parts(b)
    pa += [0] * (len(pb) - len(pa))
    pb += [0] * (len(pa) - len(pb))
    return pa < pb


def _probe_runtime(repo: Path, ef: ExternalFactors, toolchain, search) -> None:
    ef.checked.append("declared runtime version vs the interpreter present")
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        body = pyproject.read_text("utf-8", errors="replace")
        m = re.search(r'requires-python\s*=\s*["\']([^"\']+)', body)
        if m:
            spec = m.group(1)
            floor = re.search(r">=\s*([0-9]+\.[0-9]+)", spec)
            if floor:
                have = f"{sys.version_info[0]}.{sys.version_info[1]}"
                if _version_lt(have, floor.group(1)):
                    ef.findings.append(Finding(
                        "runtime",
                        f"project requires python{spec} but {have} is running",
                        "high"))


def _probe_manifest_churn(repo: Path, ef: ExternalFactors, toolchain,
                          search) -> None:
    ef.checked.append("recent dependency-manifest changes (90 days)")
    changes = manifest_changes(repo)
    # A manifest touched by the repository's first commit is not churn.
    root = run("git rev-list --max-parents=0 HEAD", repo, timeout=15,
               check_deny=False)
    roots = {ln.strip()[:7] for ln in root.stdout.splitlines() if ln.strip()}
    changes = [c for c in changes if c.sha[:7] not in roots]
    if changes:
        subjects = "; ".join(c.subject[:60] for c in changes[:3])
        ef.findings.append(Finding(
            "dependency",
            f"{len(changes)} manifest change(s) in the last 90 days: {subjects}",
            "low"))


def _probe_missing_config(repo: Path, ef: ExternalFactors, toolchain,
                          search) -> None:
    ef.checked.append("referenced config files that do not exist")
    rx = re.compile(r'["\']([\w./-]+\.(?:ya?ml|ini|cfg|toml|json|env))["\']')
    seen: set[str] = set()
    for path in _source_files(repo, limit=200):
        try:
            text = path.read_text("utf-8", errors="replace")
        except OSError:
            continue
        for m in rx.finditer(text):
            rel = m.group(1)
            if rel.startswith(("http", "/")) or rel in seen:
                continue
            seen.add(rel)
            if len(seen) > 40:
                return
            if not (repo / rel).exists() and "/" in rel:
                ef.findings.append(Finding(
                    "config", f"{rel} is referenced by {path.name} but is "
                              "not present", "low"))


def _source_files(repo: Path, limit: int = 300):
    from .search import SKIP_DIRS, SKIP_SUFFIX
    out = []
    for path in repo.rglob("*"):
        if len(out) >= limit:
            break
        if not path.is_file():
            continue
        rel = path.relative_to(repo)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIX or not path.suffix:
            continue
        out.append(path)
    return out
