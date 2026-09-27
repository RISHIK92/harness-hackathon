from __future__ import annotations

import time

import jwt

from app.config import get_settings

settings = get_settings()


def issue_session_token(account_id: str, user_id: str) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "account_id": account_id,
        "iat": now,
        "exp": now + settings.session_ttl_minutes * 60,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_session_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def api_key_matches(provided: str, stored_hash: str) -> bool:
    import hashlib

    return hashlib.sha256(provided.encode()).hexdigest() == stored_hash
