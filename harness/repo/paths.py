"""Repository-relative paths, canonicalised in one place (D-33, D-07).

`p.lstrip("./")` strips CHARACTERS, not a prefix. It turned `.github/ci.yml`
into `github/ci.yml` -- a path that does not exist, compared against one that
does, so a scope check could never match a dotfile -- and `../../etc/passwd`
into `etc/passwd`, which only looked like containment. Normalising and
containing are two different jobs, and each is done properly here.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath


def norm(raw) -> str:
    """`./a//b/` -> `a/b`, `./.github/x` -> `.github/x`, `/src/x` -> `src/x`.

    Only the spelling is canonicalised. A leading `/` is how a model writes
    "from the repository root", and has always been read that way. A `..` is
    kept, on purpose: collapsing it would quietly point at a different file,
    and `inside` is what refuses it.
    """
    s = str(raw or "").strip().replace("\\", "/")
    if not s:
        return ""
    s = PurePosixPath(s).as_posix().lstrip("/")
    return "" if s in (".", "") else s


def inside(repo: Path, rel: str) -> Path | None:
    """The resolved path when `rel` names something inside `repo`, else None.

    Resolved before it is compared, so an absolute path, a `..` anywhere in
    it, or a symlink in the repository pointing out of it cannot escape.
    """
    root = Path(repo).resolve()
    try:
        target = (root / str(rel or "")).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if target == root or root in target.parents:
        return target
    return None


# Inside the tree, but not the repository's source: git's own state -- hooks
# that run on the next commit, config that can hold a credential -- and the
# harness's run directory.
PRIVATE_DIRS = (".git", ".harness")


def in_repo(repo: Path, raw) -> Path | None:
    """The resolved path for `raw` when it is the repository's own source:
    inside the tree and outside PRIVATE_DIRS. None otherwise."""
    rel = norm(raw)
    target = inside(repo, rel) if rel else None
    if target is None:
        return None
    parts = target.relative_to(Path(repo).resolve()).parts
    if parts and parts[0] in PRIVATE_DIRS:
        return None
    return target
