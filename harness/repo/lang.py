"""Language census for the startup block (FR-11)."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

EXT_LANG = {
    ".py": "python", ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".go": "go", ".rs": "rust",
    ".java": "java", ".rb": "ruby", ".php": "php", ".c": "c", ".h": "c",
    ".cpp": "c++", ".cc": "c++", ".cs": "c#", ".kt": "kotlin",
    ".swift": "swift", ".sh": "shell", ".sql": "sql", ".scala": "scala",
}


def describe(repo: Path) -> dict:
    from .search import SKIP_DIRS
    counts: Counter = Counter()
    total = 0
    for path in Path(repo).rglob("*"):
        if total > 8000:
            break
        if not path.is_file():
            continue
        rel = path.relative_to(repo)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        lang = EXT_LANG.get(path.suffix.lower())
        if lang:
            counts[lang] += 1
            total += 1
    if not counts:
        return {"language": "unknown", "files": 0}
    top = counts.most_common(3)
    share = ", ".join(f"{name} ({100*n//max(1,total)}%)" for name, n in top)
    return {"language": share, "primary": top[0][0], "files": total}
