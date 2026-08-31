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
    daily_message_limit: int  # -1 means unlimited
    burst_limit_per_minute: int
    model: str
    max_tokens: int
    history_turns: int
    streaming: bool
    priority_queue: bool
    persistent_history: bool
    stripe_price_id: Optional[str] = None


# Groq model IDs
MODEL_FREE = os.getenv("GROQ_MODEL_FREE", "openai/gpt-oss-20b")        # Upgraded 20B model for Free tier
MODEL_PLUS = os.getenv("GROQ_MODEL_PLUS", "llama-3.3-70b-versatile")   # 70B Versatile for Plus
MODEL_FLAGSHIP = os.getenv("GROQ_MODEL_ELITE", "openai/gpt-oss-120b")  # Flagship 120B for Pro & Elite


TIERS: Dict[str, TierConfig] = {
    "free": TierConfig(
        key="free",
        display_name="Free",
        price_usd_month=0.0,
        daily_message_limit=20,
        burst_limit_per_minute=5,
        model=MODEL_FREE,       # Upgraded to 20B model
        max_tokens=512,
        history_turns=4,
        streaming=False,
        priority_queue=False,
        persistent_history=False,
        stripe_price_id=None,
    ),
    "plus": TierConfig(
        key="plus",
        display_name="Plus",
        price_usd_month=4.99,
        daily_message_limit=200,
        burst_limit_per_minute=15,
        model=MODEL_PLUS,       # 70B Llama model
        max_tokens=1024,
        history_turns=8,
        streaming=True,
        priority_queue=False,
        persistent_history=True,
        stripe_price_id=os.getenv("STRIPE_PRICE_PLUS")
        or "price_1UA93GFKLV0CUEsDrY2zR1C2",
    ),
    "pro": TierConfig(
        key="pro",
        display_name="Pro",
        price_usd_month=7.99,
        daily_message_limit=1000,
        burst_limit_per_minute=30,
        model=MODEL_FLAGSHIP,   # 120B model
        max_tokens=2048,
        history_turns=16,
        streaming=True,
        priority_queue=True,
        persistent_history=True,
        stripe_price_id=os.getenv("STRIPE_PRICE_PRO")
        or "price_1UA94kFKLV0CUEsDbKVOHzWO",
    ),
    "elite": TierConfig(
        key="elite",
        display_name="Elite",
        price_usd_month=14.99,
        daily_message_limit=-1,  # Unlimited
        burst_limit_per_minute=60,
        model=MODEL_FLAGSHIP,   # 120B model with max tokens & context memory
        max_tokens=4096,
        history_turns=30,
        streaming=True,
        priority_queue=True,
        persistent_history=True,
        stripe_price_id=os.getenv("STRIPE_PRICE_ELITE")
        or "price_1UA95bFKLV0CUEsD8xzPgdQH",
    ),
}

# Reverse lookup for Stripe webhooks
PRICE_ID_TO_TIER: Dict[str, str] = {
    tier.stripe_price_id: tier.key
    for tier in TIERS.values()
    if tier.stripe_price_id
}


def get_tier(tier_key: Optional[str]) -> TierConfig:
    return TIERS.get(tier_key or "free", TIERS["free"])
