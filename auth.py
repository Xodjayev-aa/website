"""
Authentication for Nexora Academy: Google OAuth 2.0 + stateless JWT sessions
carried in an httpOnly cookie.

Flow:
  1. GET  /api/auth/google/login    -> redirect to Google's consent screen
  2. GET  /api/auth/google/callback -> Google redirects back here with a code;
                                        we exchange it, upsert the user in
                                        Postgres, mint a JWT, set it as an
                                        httpOnly cookie, and redirect to the
                                        frontend.
  3. GET  /api/auth/me              -> reads the cookie, returns the user.
  4. POST /api/auth/logout          -> clears the cookie.

Environment variables required:
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET
  JWT_SECRET              (long random string)
  FRONTEND_URL            (e.g. https://nexora.vercel.app) — where we redirect
                          the browser back to after login/logout/checkout.
  COOKIE_DOMAIN           (optional) e.g. ".nexora.academy" if frontend and
                          backend share a parent domain. Leave unset for
                          cross-site setups using SameSite=None below.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from authlib.integrations.starlette_client import OAuth
from fastapi import Cookie, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import User

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
COOKIE_NAME = "nexora_session"
COOKIE_DOMAIN = os.getenv("COOKIE_DOMAIN") or None

# Frontend is on a different origin from the backend (Vercel + separate API
# host), so the cookie must be SameSite=None + Secure to survive the
# top-level redirect back from Google and be sent on subsequent fetch()
# calls with credentials: 'include'. This requires HTTPS in production.
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "none")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() != "false"

if not JWT_SECRET:
    # Fail loudly in production rather than silently signing tokens with an
    # empty/guessable secret.
    if os.getenv("ENV", "development") == "production":
        raise RuntimeError("JWT_SECRET must be set in production")
    JWT_SECRET = "dev-only-insecure-secret-change-me"

oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def create_jwt(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(days=JWT_EXPIRE_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        domain=COOKIE_DOMAIN,
        max_age=JWT_EXPIRE_DAYS * 24 * 3600,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=COOKIE_NAME,
        domain=COOKIE_DOMAIN,
        path="/",
    )


async def get_current_user(
    nexora_session: Optional[str] = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Required-auth dependency. Raises 401 if not signed in."""
    if not nexora_session:
        raise HTTPException(status_code=401, detail="Not signed in")
    user_id = decode_jwt(nexora_session)
    if not user_id:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


async def get_current_user_optional(
    nexora_session: Optional[str] = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Optional-auth dependency for endpoints usable while signed out."""
    if not nexora_session:
        return None
    user_id = decode_jwt(nexora_session)
    if not user_id:
        return None
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def upsert_user_from_google(db: AsyncSession, userinfo: dict) -> User:
    """Create the user on first login, or fetch the existing row on repeat logins."""
    google_sub = userinfo["sub"]
    result = await db.execute(select(User).where(User.google_sub == google_sub))
    user = result.scalar_one_or_none()

    if user:
        # Keep profile fields fresh in case they changed on Google's side.
        user.email = userinfo.get("email", user.email)
        user.name = userinfo.get("name", user.name)
        user.picture = userinfo.get("picture", user.picture)
        await db.commit()
        await db.refresh(user)
        return user

    user = User(
        id=str(uuid.uuid4()),
        google_sub=google_sub,
        email=userinfo.get("email", ""),
        name=userinfo.get("name", ""),
        picture=userinfo.get("picture", ""),
        tier="free",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def user_to_public_dict(user: User, daily_limit: int) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
        "tier": user.tier,
        "usage": user.daily_message_count,
        "dailyLimit": daily_limit,
    }
