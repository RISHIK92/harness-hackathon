"""Encryption for third-party tokens held at rest.

The GitHub App never needed this: it signs a JWT with a private key and
mints short-lived installation tokens on demand, so nothing durable is
stored. Slack is different — OAuth hands back a long-lived bot token that
we must keep to read messages later.

A bot token is enough to read every channel the app was added to, so it is
not the kind of secret to leave in a database column in plain text: a
read-only leak of one table would otherwise be a full read of a customer's
Slack.

Key derivation: the Fernet key is derived from `secret_key`, so there is
one secret to manage rather than two. Rotating `secret_key` therefore
invalidates stored tokens — connections must be re-authorised, which is
recoverable and much better than a token that outlives the secret meant to
protect it.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

settings = get_settings()


def _fernet() -> Fernet:
    # Fernet wants 32 url-safe base64 bytes; the app secret is arbitrary text.
    digest = hashlib.sha256(settings.secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Returns "" rather than raising when a value can't be decrypted.

    That happens when `secret_key` changed since the token was stored. The
    caller then behaves as if the connection is missing and prompts a
    reconnect — which is the truth — instead of a 500 nobody can act on.
    """
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError):
        return ""
