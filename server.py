"""
Nexora Academy — FastAPI backend.

Endpoints (all referenced by index.html):
  GET  /api/health               - health check, used by the frontend to
                                    decide whether to use the live AI or its
                                    offline fallback tutor.
  POST /api/chat                 - proxies a chat turn to Groq, applying the
                                    caller's tier limits/model.
  GET  /api/auth/google/login    - starts Google OAuth
  GET  /api/auth/google/callback - Google OAuth callback, sets session cookie
  GET  /api/auth/me              - current signed-in user + tier info
  POST /api/auth/logout          - clears the session cookie
  POST /api/checkout             - creates a Stripe Checkout Session
  GET  /api/billing/portal       - creates a Stripe customer portal session
  POST /api/stripe/webhook       - Stripe webhook (keeps tier in sync)

Required environment variables:
  DATABASE_URL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, JWT_SECRET,
  FRONTEND_URL, GROQ_API_KEY, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET,
  SESSION_SECRET (for the OAuth state cookie — see SessionMiddleware below)

None of these are ever sent to the browser; the frontend only ever talks to
this server's own /api/* routes.
"""

from __future__ import annotations

import os
from datetime import date

import httpx
import stripe
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from auth import (
    clear_session_cookie,
    get_current_user,
    get_current_user_optional,
    oauth,
    set_session_cookie,
    create_jwt,
    upsert_user_from_google,
    user_to_public_dict,
)
from db import get_db
from models import User
from subscription import PRICE_ID_TO_TIER, TIERS, get_tier, has_quota_remaining

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Nexora Academy API")

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
SESSION_SECRET = os.getenv("SESSION_SECRET", os.getenv("JWT_SECRET", "dev-only-secret"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

stripe.api_key = STRIPE_SECRET_KEY

# Required by Authlib to store OAuth state/nonce between the /login redirect
# and the /callback request. This is separate from our own JWT session
# cookie above and only lives for the duration of the OAuth handshake.
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="none", https_only=True)

# Frontend is on a different origin (Vercel/static host) from this API, so
# CORS must explicitly allow that origin and allow credentials (cookies).
# "*" cannot be used together with allow_credentials=True.
_allowed_origins = [FRONTEND_URL]
if extra := os.getenv("EXTRA_CORS_ORIGINS", ""):
    _allowed_origins.extend([o.strip() for o in extra.split(",") if o.strip()])

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "ok", "ai_configured": bool(GROQ_API_KEY)}


# ---------------------------------------------------------------------------
# Auth: Google OAuth + JWT cookie session
# ---------------------------------------------------------------------------

@app.get("/api/auth/google/login")
async def google_login(request: Request):
    redirect_uri = str(request.url_for("google_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/api/auth/google/callback")
async def google_callback(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception:
        return Response(
            status_code=302,
            headers={"Location": f"{FRONTEND_URL}/?auth=error&message=Google+sign-in+failed"},
        )

    userinfo = token.get("userinfo")
    if not userinfo or not userinfo.get("sub"):
        return Response(
            status_code=302,
            headers={"Location": f"{FRONTEND_URL}/?auth=error&message=No+profile+returned"},
        )

    user = await upsert_user_from_google(db, userinfo)
    jwt_token = create_jwt(user.id)

    redirect = Response(
        status_code=302,
        headers={"Location": f"{FRONTEND_URL}/?auth=success"},
    )
    set_session_cookie(redirect, jwt_token)
    return redirect


@app.get("/api/auth/me")
async def get_me(user: User = Depends(get_current_user_optional)):
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in")
    tier = get_tier(user.tier)
    _reset_daily_usage_if_new_day(user)
    return user_to_public_dict(user, tier.daily_message_limit)


@app.post("/api/auth/logout")
async def logout():
    response = Response(status_code=200, content='{"ok":true}', media_type="application/json")
    clear_session_cookie(response)
    return response


# ---------------------------------------------------------------------------
# Chat (Groq)
# ---------------------------------------------------------------------------

class ChatHistoryTurn(BaseModel):
    role: str
    parts: list[dict]


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    language: str = "en"
    history: list[ChatHistoryTurn] = []
    context: str = ""


def _reset_daily_usage_if_new_day(user: User) -> None:
    today = date.today()
    if user.last_message_date != today:
        user.last_message_date = today
        user.daily_message_count = 0


@app.post("/api/chat")
async def chat(
    body: ChatRequest,
    user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if not GROQ_API_KEY:
        raise HTTPException(status_code=503, detail="AI backend is not configured")

    # Signed-out visitors get the "free" tier's limits, tracked per-session
    # only for the response shape; real quota enforcement requires sign-in
    # for anything beyond a nominal trial, since we can't track anonymous
    # usage server-side without a cookie/user row.
    tier_key = user.tier if user else "free"
    tier = get_tier(tier_key)

    if user:
        _reset_daily_usage_if_new_day(user)
        if not has_quota_remaining(user.daily_message_count, user.tier):
            raise HTTPException(
                status_code=429,
                detail="Daily message limit reached for your plan. Upgrade for more.",
            )

    system_prompt = (
        "You are the Nexora Academy assistant, a friendly, patient tutor for "
        "languages, coding, and math/physics. Respond in the language code "
        f"'{body.language}' unless the user clearly writes in another language. "
        "Keep answers concise and encouraging. Use the following lesson "
        f"context if relevant, otherwise ignore it: {body.context}"
    )

    messages = [{"role": "system", "content": system_prompt}]
    for turn in body.history[-tier.history_turns:]:
        role = "assistant" if turn.role == "model" else "user"
        text = "".join(p.get("text", "") for p in turn.parts)
        if text:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": body.message})

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": tier.model,
                    "messages": messages,
                    "max_tokens": tier.max_tokens,
                },
            )
        resp.raise_for_status()
        data = resp.json()
        answer = data["choices"][0]["message"]["content"].strip()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"AI provider error: {e.response.status_code}")
    except Exception:
        raise HTTPException(status_code=502, detail="AI provider request failed")

    if user:
        user.daily_message_count += 1
        await db.commit()

    return {"answer": answer}


# ---------------------------------------------------------------------------
# Stripe: checkout, billing portal, webhook
# ---------------------------------------------------------------------------

@app.post("/api/checkout")
async def create_checkout_session(
    price_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Payments are not configured")
    if price_id not in PRICE_ID_TO_TIER:
        raise HTTPException(status_code=400, detail="Unknown plan")

    try:
        if not user.stripe_customer_id:
            customer = stripe.Customer.create(email=user.email, name=user.name)
            user.stripe_customer_id = customer.id
            await db.commit()

        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=user.stripe_customer_id,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{FRONTEND_URL}/?checkout=success",
            cancel_url=f"{FRONTEND_URL}/?checkout=cancelled",
            client_reference_id=user.id,
            allow_promotion_codes=True,
        )
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=502, detail=f"Stripe error: {e.user_message or str(e)}")

    return {"url": session.url}


@app.get("/api/billing/portal")
async def billing_portal(user: User = Depends(get_current_user)):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Payments are not configured")
    if not user.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No billing account on file yet")

    try:
        session = stripe.billing_portal.Session.create(
            customer=user.stripe_customer_id,
            return_url=f"{FRONTEND_URL}/?billing=done",
        )
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=502, detail=f"Stripe error: {e.user_message or str(e)}")

    return {"url": session.url}


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type == "checkout.session.completed":
        customer_id = obj.get("customer")
        client_reference_id = obj.get("client_reference_id")
        subscription_id = obj.get("subscription")

        result = await db.execute(select(User).where(User.id == client_reference_id))
        user = result.scalar_one_or_none()
        if user:
            user.stripe_customer_id = customer_id
            user.stripe_subscription_id = subscription_id
            # The line item's price determines the tier; fetch it from Stripe.
            if subscription_id:
                sub = stripe.Subscription.retrieve(subscription_id)
                price_id = sub["items"]["data"][0]["price"]["id"]
                user.tier = PRICE_ID_TO_TIER.get(price_id, user.tier)
            await db.commit()

    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        customer_id = obj.get("customer")
        result = await db.execute(select(User).where(User.stripe_customer_id == customer_id))
        user = result.scalar_one_or_none()
        if user:
            if event_type == "customer.subscription.deleted" or obj.get("status") in (
                "canceled",
                "unpaid",
            ):
                user.tier = "free"
                user.stripe_subscription_id = None
            else:
                items = obj.get("items", {}).get("data", [])
                if items:
                    price_id = items[0]["price"]["id"]
                    user.tier = PRICE_ID_TO_TIER.get(price_id, user.tier)
            await db.commit()

    return {"received": True}
