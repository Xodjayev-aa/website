"""
Google OAuth (via Authlib) + a signed JWT kept in an httpOnly cookie.

Why a cookie instead of a bearer token in localStorage: index.html is a
single static file with no build step, and storing a long-lived auth token
in localStorage/JS-readable storage is an easy XSS target. An httpOnly
cookie can't be read by JavaScript at all, so a plain `fetch(..., {credentials:
'include'})` call is all the frontend needs to do.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, HTTPException, Request
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from db import User, get_session, get_user_by_id

JWT_SECRET = os.getenv("JWT_SECRET", "")
if not JWT_SECRET:
    raise RuntimeError(
        "JWT_SECRET is not set. Generate one with:\n"
        "  python -c \"import secrets; print(secrets.token_hex(32))\"\n"
        "and put it in your .env as JWT_SECRET=..."
    )

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_SECONDS = 60 * 60 * 24 * 30  # 30 days
SESSION_COOKIE_NAME = "nexora_session"
IS_PRODUCTION = os.getenv("ENV", "development") == "production"

oauth = OAuth()
oauth.register(
    name="google",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    client_kwargs={"scope": "openid email profile"},
)


def create_session_token(user_id: int) -> str:
    payload = {"sub": str(user_id), "exp": int(time.time()) + JWT_EXPIRE_SECONDS}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_session_token(token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return int(payload["sub"])
    except (JWTError, KeyError, ValueError, TypeError):
        return None


def set_session_cookie(response, user_id: int) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_token(user_id),
        httponly=True,
        samesite="lax",
        secure=IS_PRODUCTION,
        max_age=JWT_EXPIRE_SECONDS,
        path="/",
    )


async def get_current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    """Required-auth dependency — raises 401 if there's no valid session."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user_id = decode_session_token(token) if token else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Session no longer valid — please sign in again.")
    return user


async def get_optional_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Optional[User]:
    """Soft-auth dependency for routes that behave differently when logged in
    but shouldn't hard-fail when logged out (e.g. /api/auth/me)."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user_id = decode_session_token(token) if token else None
    if user_id is None:
        return None
    return await get_user_by_id(session, user_id)
