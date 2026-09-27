"""Human-shareable meeting ids: `abcd-efgh`.

Read aloud on a call, typed from a chat message, and short enough to say
twice. The alphabet deliberately excludes characters that are
indistinguishable when spoken or in most fonts — 0/O, 1/l/I — because the
first thing anyone does with one of these is dictate it to someone else.
"""
from __future__ import annotations

import secrets

# 22 letters + 8 digits, minus the confusable ones.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_GROUP = 4


def new_slug() -> str:
    left = "".join(secrets.choice(_ALPHABET) for _ in range(_GROUP))
    right = "".join(secrets.choice(_ALPHABET) for _ in range(_GROUP))
    return f"{left}-{right}"


def normalise(slug: str) -> str:
    """Accept what a human typed: spaces, caps, a missing hyphen."""
    cleaned = "".join(c for c in slug.lower() if c.isalnum())
    if len(cleaned) != _GROUP * 2:
        return slug.strip().lower()
    return f"{cleaned[:_GROUP]}-{cleaned[_GROUP:]}"
