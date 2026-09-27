"""Authentication router — signup, login, and Sign in with GitHub."""
from __future__ import annotations

import secrets

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.config import get_settings
from app.database import get_session
from app.models import User, UserCreate, UserRead
from app.core.auth import hash_password, verify_password, create_access_token, get_current_user
from app.core.workspace import ensure_personal_workspace

log = structlog.get_logger()
router = APIRouter()
settings = get_settings()

_GITHUB_OAUTH_STATE_COOKIE = "gh_oauth_state"


@router.post("/signup", response_model=UserRead, status_code=201)
async def signup(payload: UserCreate, session: AsyncSession = Depends(get_session)):
    # Check duplicate email
    result = await session.execute(select(User).where(User.email == payload.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(email=payload.email.lower().strip(), hashed_password=hash_password(payload.password))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    # A user with no workspace has nowhere to put a repo, so it is created
    # here rather than lazily at first use.
    await ensure_personal_workspace(session, user)
    return user


@router.post("/login")
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(User).where(User.email == form_data.username.lower().strip()))
    user = result.scalar_one_or_none()
    if user and user.hashed_password is None:
        raise HTTPException(status_code=400, detail="This account uses GitHub sign-in — use Continue with GitHub instead")
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Users created before workspaces existed would otherwise log in fine
    # and then have nowhere to put anything.
    workspace = await ensure_personal_workspace(session, user)
    token = create_access_token(user.id, user.email)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": user.id, "email": user.email},
        "workspace": {"id": workspace.id, "name": workspace.name},
    }


@router.get("/me", response_model=UserRead)
async def me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user."""
    return current_user


# ─── Sign in with GitHub ────────────────────────────────────────────────────
# An additional login method alongside email/password, not a replacement.
# Uses the GitHub App's own OAuth capability (github_app_client_id/secret)
# rather than a separate OAuth App — one App handles both login and the
# "Connect GitHub" installation flow in routers/github_app.py.

@router.get("/github/login")
async def github_login():
    if not settings.github_app_client_id:
        raise HTTPException(status_code=503, detail="GitHub sign-in is not configured on this deployment")
    state = secrets.token_urlsafe(24)
    redirect_uri = f"{settings.public_base_url}/api/auth/github/callback"
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={settings.github_app_client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
        "&scope=read:user user:email"
    )
    resp = RedirectResponse(url)
    # httpOnly + short-lived: this cookie's only job is proving the callback
    # belongs to the redirect we just issued (CSRF guard), not carrying
    # anything sensitive.
    resp.set_cookie(_GITHUB_OAUTH_STATE_COOKIE, state, httponly=True, samesite="lax", max_age=600)
    return resp


@router.get("/github/callback")
async def github_callback(
    request: Request,
    code: str,
    state: str,
    session: AsyncSession = Depends(get_session),
):
    cookie_state = request.cookies.get(_GITHUB_OAUTH_STATE_COOKIE)
    if not cookie_state or cookie_state != state:
        raise HTTPException(status_code=400, detail="Invalid or expired GitHub sign-in attempt — please try again")

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_app_client_id,
                "client_secret": settings.github_app_client_secret,
                "code": code,
                "redirect_uri": f"{settings.public_base_url}/api/auth/github/callback",
            },
        )
        token_resp.raise_for_status()
        access_token = token_resp.json().get("access_token")
        if not access_token:
            # Never log token_resp.json() — it may contain error detail but
            # could also echo back request params; keep this generic.
            raise HTTPException(status_code=400, detail="GitHub did not return an access token")

        gh_headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"}
        user_resp = await client.get("https://api.github.com/user", headers=gh_headers)
        user_resp.raise_for_status()
        gh_user = user_resp.json()

        email = gh_user.get("email")
        if not email:
            # Private-email users don't expose it on /user. /user/emails
            # can supply it — but ONLY if the App holds the "Email
            # addresses" account permission. A GitHub App's user token
            # derives access from the App's PERMISSIONS, not from OAuth
            # scopes, so the `scope=user:email` in the authorize URL buys
            # nothing here and this call 403s on an App that wasn't granted
            # it. Observed live: 403 Forbidden, which crashed sign-in
            # outright for anyone with a private email.
            #
            # Treated as optional rather than required: sign-in must not
            # depend on a permission we can add later, and GitHub gives
            # every account a stable noreply address we can fall back to.
            try:
                emails_resp = await client.get("https://api.github.com/user/emails", headers=gh_headers)
                if emails_resp.status_code == 200:
                    emails = emails_resp.json()
                    primary = next((e["email"] for e in emails if e.get("primary") and e.get("verified")), None)
                    email = primary or next((e["email"] for e in emails if e.get("verified")), None)
                else:
                    log.info("auth.github_emails_unavailable", status=emails_resp.status_code)
            except httpx.HTTPError as exc:
                log.info("auth.github_emails_failed", error=str(exc))

    if not email:
        # GitHub's own no-reply convention. Stable, unique per account, and
        # deliverable-to-nobody by design — which is correct here, since we
        # use email as an identity key, not as a way to contact anyone.
        login = gh_user.get("login")
        if not login:
            raise HTTPException(status_code=400, detail="GitHub did not return a usable account identity")
        email = f"{gh_user['id']}+{login}@users.noreply.github.com"

    github_id = str(gh_user["id"])
    email = email.lower().strip()

    result = await session.execute(select(User).where(User.github_id == github_id))
    user = result.scalar_one_or_none()

    if not user:
        # Not linked yet — match by email so a user who already signed up
        # with email/password gets linked instead of getting a duplicate
        # account when they later click "Continue with GitHub".
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user:
            user.github_id = github_id
            user.github_login = gh_user.get("login")
            session.add(user)
        else:
            user = User(email=email, hashed_password=None, github_id=github_id, github_login=gh_user.get("login"))
            session.add(user)
        await session.commit()
        await session.refresh(user)

    await ensure_personal_workspace(session, user)
    token = create_access_token(user.id, user.email)

    # Fragment, not query string: the token must never land in a server
    # access log or get forwarded in a Referer header.
    resp = RedirectResponse(f"{settings.client_base_url}/auth/callback#token={token}")
    resp.delete_cookie(_GITHUB_OAUTH_STATE_COOKIE)
    return resp
