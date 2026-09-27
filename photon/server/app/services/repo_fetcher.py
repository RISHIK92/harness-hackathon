from __future__ import annotations
import os
import re
import shutil
from pathlib import Path
from typing import Optional
import structlog
import git
from app.config import get_settings

log = structlog.get_logger()
settings = get_settings()

_CREDENTIAL_IN_URL_RE = re.compile(r"https://[^@/]+@")


def _redact(url: str) -> str:
    """Strip an embedded token/credential before a clone URL ever reaches a
    log line. Bug found in a code audit: this used to log the token-
    embedded URL directly (both a static PAT and, once GitHub App
    installation tokens exist, an equally sensitive short-lived token)."""
    return _CREDENTIAL_IN_URL_RE.sub("https://", url)


def clone_github_repo(url: str, repo_id: str, token: Optional[str] = None) -> str:
    """Clone a GitHub repo (public or private) to local storage. Returns local path."""
    storage = Path(settings.repos_storage_path)
    storage.mkdir(parents=True, exist_ok=True)
    dest = storage / repo_id

    if dest.exists():
        shutil.rmtree(dest)

    if token:
        # Inject token into URL for private repos
        if url.startswith("https://"):
            url = url.replace("https://", f"https://{token}@")

    log.info("cloning_repo", url=_redact(url), dest=str(dest))
    git.Repo.clone_from(url, str(dest), depth=1)
    log.info("clone_complete", dest=str(dest))
    return str(dest)


def use_local_path(source_path: str, repo_id: str) -> str:
    """Symlink or copy a local path into managed storage. Returns local path."""
    storage = Path(settings.repos_storage_path)
    storage.mkdir(parents=True, exist_ok=True)
    dest = storage / repo_id

    if dest.exists():
        if dest.is_symlink():
            dest.unlink()
        else:
            shutil.rmtree(dest)

    # Prefer symlink to avoid duplication
    try:
        os.symlink(os.path.abspath(source_path), str(dest))
    except OSError:
        shutil.copytree(source_path, str(dest))

    log.info("local_mount_ready", dest=str(dest))
    return str(dest)
