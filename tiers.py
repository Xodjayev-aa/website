# Tier limits and valid Groq production model strings

TIER_CONFIG = {
    "free": {
        "daily_limit": 15,
        "model": "llama-3.1-8b-instant",
        "name": "Free Plan"
    },
    "pro": {
        "daily_limit": 200,
        "model": "llama-3.3-70b-versatile",
        "name": "Pro Tier"
    },
    "elite": {
        "daily_limit": None,  # Unlimited
        "model": "llama-3.3-70b-versatile",
        "name": "Elite Tier"
    }
}

def get_tier_config(tier_name: str) -> dict:
    return TIER_CONFIG.get(str(tier_name).lower(), TIER_CONFIG["free"])

def check_quota(daily_usage: int, tier_name: str) -> bool:
    config = get_tier_config(tier_name)
    limit = config["daily_limit"]
    if limit is None:
        return True
    return daily_usage < limit
