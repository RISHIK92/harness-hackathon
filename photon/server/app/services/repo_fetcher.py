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


def _github_auth_env(token: str) -> dict[str, str]:
    import base64

    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
        "GIT_TERMINAL_PROMPT": "0",
    }


def clone_github_repo(url: str, repo_id: str, token: Optional[str] = None) -> str:
    """Clone a GitHub repo (public or private) to local storage. Returns local path."""
    storage = Path(settings.repos_storage_path)
    storage.mkdir(parents=True, exist_ok=True)
    dest = storage / repo_id

    if dest.exists():
        shutil.rmtree(dest)

    # The token travels as an HTTP header set through git's environment
    # config — never in the URL, where it was written into the clone's
    # .git/config (readable by anything that later runs in that checkout)
    # and sent to whatever host the URL named. Scoped to github.com.
    env = _github_auth_env(token) if token and url.startswith("https://github.com/") else None

    log.info("cloning_repo", url=_redact(url), dest=str(dest))
    git.Repo.clone_from(url, str(dest), env=env, depth=1)
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
