from __future__ import annotations
import os
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel.ext.asyncio.session import AsyncSession
from typing import Any

from app.database import get_session
from app.models import Repo
from app.config import get_settings

router = APIRouter()
settings = get_settings()

# ---------- File tree ----------

IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache", ".pytest_cache", "dist", "build", ".next"}
CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".cpp", ".c", ".h",
             ".md", ".json", ".yaml", ".yml", ".toml", ".html", ".css", ".sh", ".env"}

def _build_tree(root: str, rel: str = "") -> list[dict[str, Any]]:
    abs_dir = os.path.join(root, rel) if rel else root
    entries: list[dict[str, Any]] = []
    try:
        items = sorted(os.listdir(abs_dir))
    except PermissionError:
        return []
    dirs = [i for i in items if os.path.isdir(os.path.join(abs_dir, i)) and i not in IGNORE_DIRS]
    files = [i for i in items if os.path.isfile(os.path.join(abs_dir, i)) and os.path.splitext(i)[1].lower() in CODE_EXTS]
    for d in dirs:
        child_rel = f"{rel}/{d}" if rel else d
        entries.append({"name": d, "path": child_rel, "type": "dir", "children": _build_tree(root, child_rel)})
    for f in files:
        child_rel = f"{rel}/{f}" if rel else f
        entries.append({"name": f, "path": child_rel, "type": "file"})
    return entries


@router.get("/{repo_id}/tree")
async def get_file_tree(
    repo_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Return the full file tree for a repo (code files only)."""
    repo = await session.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    if not repo.local_path or not os.path.isdir(repo.local_path):
        raise HTTPException(status_code=404, detail="Repo local path not found")
    return {"repo_id": repo_id, "tree": _build_tree(repo.local_path)}


@router.get("/{repo_id}")
async def get_file(
    repo_id: str,
    path: str = Query(..., description="Relative path within the repo"),
    start_line: int = Query(default=1, ge=1),
    end_line: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    """Return file content with optional line range. Used by deep links."""
    repo = await session.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    full_path = os.path.join(repo.local_path or "", path.lstrip("/"))
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="File not found")

    # Basic security: ensure path doesn't escape repo root
    repo_root = os.path.realpath(repo.local_path)
    real = os.path.realpath(full_path)
    if not real.startswith(repo_root):
        raise HTTPException(status_code=403, detail="Path traversal denied")

    with open(full_path, "r", errors="replace") as f:
        lines = f.readlines()

    if end_line == 0:
        end_line = len(lines)

    total_lines = len(lines)
    selected = lines[start_line - 1 : end_line]

    # Detect language from extension
    ext = os.path.splitext(path)[1].lstrip(".")
    lang_map = {
        "py": "python", "js": "javascript", "ts": "typescript",
        "tsx": "tsx", "jsx": "jsx", "go": "go", "rs": "rust",
        "java": "java", "cpp": "cpp", "c": "c", "md": "markdown",
        "json": "json", "yaml": "yaml", "yml": "yaml", "toml": "toml",
    }
    language = lang_map.get(ext.lower(), "plaintext")

    return {
        "repo_id": repo_id,
        "path": path,
        "language": language,
        "total_lines": total_lines,
        "start_line": start_line,
        "end_line": end_line,
        "content": "".join(selected),
    }
