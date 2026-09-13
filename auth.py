"""
Google OAuth 2.0 + JWT session cookies.

Runs on Vercel's serverless functions, so nothing here depends on
in-memory state surviving between requests: the OAuth "state" value is a
signed, self-contained token (not a server-side session), and every
request creates its own short-lived DB connection.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import Cookie, Depends, HTTPException, Response
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
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
COOKIE_NAME = "nexora_session"
COOKIE_DOMAIN = os.getenv("COOKIE_DOMAIN") or None
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() != "false"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

if not JWT_SECRET:
    if os.getenv("ENV", "development") == "production":
        raise RuntimeError("JWT_SECRET must be set in production")
    JWT_SECRET = "dev-only-insecure-secret-change-me"


# ---------------------------------------------------------------------------
# JWT session cookie
# ---------------------------------------------------------------------------

def create_jwt(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "iat": now, "exp": now + timedelta(days=JWT_EXPIRE_DAYS)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> Optional[str]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM]).get("sub")
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
    response.delete_cookie(key=COOKIE_NAME, domain=COOKIE_DOMAIN, path="/")


# ---------------------------------------------------------------------------
# Stateless OAuth "state" token — replaces server-side session storage so
# the login/callback pair works even when they land on different serverless
# instances with no shared memory.
# ---------------------------------------------------------------------------

def _sign(value: str) -> str:
    sig = hmac.new(JWT_SECRET.encode(), value.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")


def make_oauth_state() -> str:
    nonce = uuid.uuid4().hex
    ts = str(int(time.time()))
    raw = f"{nonce}.{ts}"
    return f"{raw}.{_sign(raw)}"


def verify_oauth_state(state: str, max_age_seconds: int = 600) -> bool:
    try:
        nonce, ts, sig = state.split(".")
    except ValueError:
        return False
    raw = f"{nonce}.{ts}"
    if not hmac.compare_digest(_sign(raw), sig):
        return False
    return (time.time() - int(ts)) <= max_age_seconds


def build_google_authorize_url(redirect_uri: str) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": make_oauth_state(),
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_userinfo(code: str, redirect_uri: str) -> dict:
    """Exchanges an OAuth code for an access token, then fetches the profile."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        userinfo_resp.raise_for_status()
        return userinfo_resp.json()


# ---------------------------------------------------------------------------
# Current-user dependencies
# ---------------------------------------------------------------------------

async def get_current_user(
    nexora_session: Optional[str] = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
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
    if not nexora_session:
        return None
    user_id = decode_jwt(nexora_session)
    if not user_id:
        return None
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def upsert_user_from_google(db: AsyncSession, userinfo: dict) -> User:
    google_sub = userinfo["sub"]
    result = await db.execute(select(User).where(User.google_sub == google_sub))
    user = result.scalar_one_or_none()

    if user:
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
