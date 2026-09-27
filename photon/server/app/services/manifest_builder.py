from __future__ import annotations
from pathlib import Path
from typing import Iterator
import pathspec
import structlog

log = structlog.get_logger()

# Always-skip patterns regardless of .gitignore
ALWAYS_EXCLUDE = pathspec.PathSpec.from_lines("gitwildmatch", [
    ".git/",
    ".git/**",
    "node_modules/",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    "*.min.js",
    "*.min.css",
    "*.map",
    "dist/",
    "build/",
    ".next/",
    "coverage/",
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "*.egg-info/",
    ".venv/",
    "venv/",
    ".env",
    "*.log",
    "*.jpg",
    "*.jpeg",
    "*.png",
    "*.gif",
    "*.svg",
    "*.ico",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.eot",
    "*.pdf",
    "*.zip",
    "*.tar.gz",
    "*.sql",
])

# Maximum file size to process (bytes)
MAX_FILE_BYTES = 500_000


def build_manifest(repo_path: str) -> list[dict]:
    """
    Walk repo_path respecting .gitignore rules.
    Returns list of dicts with: path (relative), abs_path, size_bytes, language.
    """
    root = Path(repo_path)

    # Load .gitignore if present
    gitignore_path = root / ".gitignore"
    if gitignore_path.exists():
        with open(gitignore_path) as f:
            user_spec = pathspec.PathSpec.from_lines("gitwildmatch", f)
    else:
        user_spec = pathspec.PathSpec.from_lines("gitwildmatch", [])

    from app.core.parser.language_detector import detect_language

    manifest: list[dict] = []

    for abs_path in sorted(root.rglob("*")):
        if not abs_path.is_file():
            continue

        rel = str(abs_path.relative_to(root))

        # Apply exclusion rules
        if ALWAYS_EXCLUDE.match_file(rel):
            continue
        if user_spec.match_file(rel):
            continue

        size = abs_path.stat().st_size
        if size > MAX_FILE_BYTES:
            log.debug("manifest.skip_large_file", path=rel, size=size)
            continue

        language = detect_language(str(abs_path))
        if language == "unknown":
            continue

        manifest.append({
            "path": rel,
            "abs_path": str(abs_path),
            "size_bytes": size,
            "language": language,
        })

    log.info("manifest_built", file_count=len(manifest))
    return manifest
