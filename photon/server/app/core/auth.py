"""JWT authentication utilities."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.database import get_session

settings = get_settings()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


# ─── Password helpers ─────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: Optional[str]) -> bool:
    # A GitHub-only account has hashed_password=None — must fail the check,
    # not crash bcrypt with a None hash.
    if not hashed:
        return False
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ─── Token helpers ────────────────────────────────────────────────────────────

def create_access_token(user_id: str, email: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": user_id, "email": email, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


# A token minted for the Chrome extension (routers/extension.py) carries
# this scope. It is a whisper-only credential: it lives in a browser
# extension's storage, so if it leaks it must not open the repos, the agent
# jobs or the workspace settings — only exactly the routes the extension
# calls. A path PREFIX used to be the rule, and "/api/extension/" let a
# leaked token mint fresh pairing codes (new devices that outlive revoking
# the leaked one), and "/api/whisper/" let it dispatch paid meeting bots and
# read other sessions' webhook secrets.
EXTENSION_SCOPE = "whisper-extension"
EXTENSION_ROUTES = tuple(re.compile(p) for p in (
    r"^/api/extension/me$",
    r"^/api/extension/meet-session$",
    r"^/api/whisper/sessions/[^/]+/(thread|lines)$",
    r"^/api/whisper/threads/[^/]+/(messages|ask)$",
))


def extension_may_call(path: str) -> bool:
    return any(p.match(path) for p in EXTENSION_ROUTES)


def create_extension_token(user_id: str, email: str, workspace_id: str, device_id: str,
                           days: int = 30) -> tuple[str, datetime]:
    expire = datetime.utcnow() + timedelta(days=days)
    payload = {"sub": user_id, "email": email, "exp": expire, "scope": EXTENSION_SCOPE,
               "ws": workspace_id, "jti": device_id}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm), expire


async def _check_extension_token(payload: dict, request: Request, session: AsyncSession) -> None:
    """Scope, workspace and revocation for an extension token. Raises 401."""
    from app.models import ExtensionDevice

    denied = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                           detail="This extension token cannot be used here")
    if not extension_may_call(request.url.path):
        raise denied
    # Pinned to the workspace it was paired in: a header naming another
    # workspace (or none, which would fall back to the personal one) is refused.
    requested = request.headers.get("x-workspace-id") or request.query_params.get("workspace_id")
    if requested != payload.get("ws"):
        raise denied
    device = await session.get(ExtensionDevice, payload.get("jti") or "")
    if device is None or device.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="This browser was disconnected from Photon — pair it again")
    device.last_seen_at = datetime.utcnow()
    session.add(device)
    await session.commit()


# ─── Current-user dependency ──────────────────────────────────────────────────

async def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
):
    from app.models import User  # local import to avoid circular

    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        user_id: Optional[str] = payload.get("sub")
        if user_id is None:
            raise credentials_exc
    except JWTError:
        raise credentials_exc
    if payload.get("scope") == EXTENSION_SCOPE:
        await _check_extension_token(payload, request, session)
    elif payload.get("scope"):
        raise credentials_exc              # an unknown scope is never a full token

    user = await session.get(User, user_id)
    if user is None:
        raise credentials_exc
    return user
