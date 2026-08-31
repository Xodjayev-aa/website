"""
Subscription tier configuration for Nexora Academy.

This is the single source of truth for what each plan gets. Both the
/api/chat route and the /api/auth/me route read from here, so changing a
limit or a model in one place changes it everywhere.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class TierConfig:
    key: str
    display_name: str
    price_usd_month: float
    daily_message_limit: int   # -1 means unlimited
    burst_limit_per_minute: int
    model: str
    max_tokens: int
    history_turns: int
    streaming: bool
    priority_queue: bool
    persistent_history: bool
    stripe_price_id: Optional[str] = None


# Groq model IDs — see https://console.groq.com/docs/models for the current list.
# Free gets a small, very fast model; paid tiers step up in quality.
MODEL_FREE = os.getenv("GROQ_MODEL_FREE", "llama-3.1-8b-instant")
MODEL_STANDARD = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
MODEL_ELITE = os.getenv("GROQ_MODEL_ELITE", "llama-3.3-70b-versatile")

# tiers.py

TIERS: Dict[str, TierConfig] = {
    "free": TierConfig(
        key="free",
        display_name="Free Plan",
        daily_message_limit=15,
        burst_limit_per_minute=5,
        model="llama-3.1-8b-instant",  # <--- Fast, cheap 8B model for free users
        max_tokens=1024,
        history_turns=4,
        streaming=False,
        priority_queue=False,
        persistent_history=False,
        stripe_price_id=None,
    ),
    "plus": TierConfig(
        key="plus",
        display_name="Plus Plan",
        daily_message_limit=100,
        burst_limit_per_minute=15,
        model="llama-3.3-70b-versatile", # <--- Powerful 70B model for paying users
        max_tokens=2048,
        history_turns=10,
        streaming=True,
        priority_queue=False,
        persistent_history=True,
        stripe_price_id=PLUS_PRICE_ID,
    ),
    "pro": TierConfig(
        key="pro",
        display_name="Pro Plan",
        daily_message_limit=500,
        burst_limit_per_minute=30,
        model="llama-3.3-70b-versatile",
        max_tokens=4096,
        history_turns=20,
        streaming=True,
        priority_queue=True,
        persistent_history=True,
        stripe_price_id=PRO_PRICE_ID,
    ),
    "elite": TierConfig(
        key="elite",
        display_name="Elite Plan",
        daily_message_limit=-1,
        burst_limit_per_minute=60,
        model="llama-3.3-70b-versatile",
        max_tokens=8192,
        history_turns=30,
        streaming=True,
        priority_queue=True,
        persistent_history=True,
        stripe_price_id=ELITE_PRICE_ID,
    ),
}

TIERS: Dict[str, TierConfig] = {
    "free": TierConfig(
        key="free",
        display_name="Free",
        price_usd_month=0,
        daily_message_limit=20,
        burst_limit_per_minute=4,
        model=MODEL_FREE,
        max_tokens=512,
        history_turns=4,
        streaming=False,
        priority_queue=False,
        persistent_history=False,
    ),
    "plus": TierConfig(
        key="plus",
        display_name="Plus",
        price_usd_month=6,
        daily_message_limit=200,
        burst_limit_per_minute=10,
        model=MODEL_STANDARD,
        max_tokens=1024,
        history_turns=8,
        streaming=True,
        priority_queue=False,
        persistent_history=True,
        stripe_price_id=os.getenv("price_1UA93GFKLV0CUEsDrY2zR1C2") or None,
    ),
    "pro": TierConfig(
        key="pro",
        display_name="Pro",
        price_usd_month=15,
        daily_message_limit=1000,
        burst_limit_per_minute=20,
        model=MODEL_STANDARD,
        max_tokens=2048,
        history_turns=16,
        streaming=True,
        priority_queue=True,
        persistent_history=True,
        stripe_price_id=os.getenv("price_1UA94kFKLV0CUEsDbKVOHzWO") or None,
    ),
    "elite": TierConfig(
        key="elite",
        display_name="Elite",
        price_usd_month=39,
        daily_message_limit=-1,
        burst_limit_per_minute=40,
        model=MODEL_ELITE,
        max_tokens=4096,
        history_turns=24,
        streaming=True,
        priority_queue=True,
        persistent_history=True,
        stripe_price_id=os.getenv("price_1UA95bFKLV0CUEsD8xzPgdQH") or None,
    ),
}

# Reverse lookup used by the Stripe webhook to turn "which price did they buy"
# into "which tier do we grant". Built once at import time.
PRICE_ID_TO_TIER: Dict[str, str] = {
    tier.stripe_price_id: tier.key for tier in TIERS.values() if tier.stripe_price_id
}


def get_tier(tier_key: Optional[str]) -> TierConfig:
    """Always returns a valid TierConfig — unknown/None keys fall back to Free
    rather than raising, since this is called on every chat request."""
    return TIERS.get(tier_key or "free", TIERS["free"])
