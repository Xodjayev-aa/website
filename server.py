import os
import httpx
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict

import db
from tiers import get_tier_config, check_quota
from auth import get_current_user

app = FastAPI(title="Nexora Academy Backend API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

class ChatRequest(BaseModel):
    message: str
    history: Optional[List[Dict[str, str]]] = []
    lang: Optional[str] = "en"

class ProfileOnboarding(BaseModel):
    english_level: str
    uzbek_level: str
    russian_level: str
    coding_level: str
    maths_level: str

@app.on_event("startup")
async def startup_event():
    await db.init_db()

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "Nexora Academy"}

@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest, current_user: dict = Depends(get_current_user)):
    user_id = int(current_user["sub"])
    tier = current_user.get("tier", "free")
    pool = await db.get_db_pool()
    daily_usage = 0

    if pool:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT daily_requests_count FROM user_stats WHERE user_id = $1 AND last_active_date = CURRENT_DATE", 
                user_id
            )
            if row:
                daily_usage = row["daily_requests_count"]

    # Enforce strict 402 error when daily limit is exceeded
    if not check_quota(daily_usage, tier):
        raise HTTPException(status_code=402, detail="Daily AI request quota reached for your tier.")

    tier_cfg = get_tier_config(tier)
    model_name = tier_cfg["model"]

    if not GROQ_API_KEY:
        return {"reply": "Server side AI key is unconfigured. Running in local fallback mode."}

    messages = [{"role": "system", "content": f"You are Nexora AI tutor. Current language context: {req.lang}."}]
    for h in req.history:
        messages.append({"role": h.get("role", "user"), "content": h.get("text", "")})
    messages.append({"role": "user", "content": req.message})

    async with httpx.AsyncClient() as client:
        res = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": model_name,
                "messages": messages,
                "temperature": 0.7
            },
            timeout=30.0
        )
        if res.status_code != 200:
            raise HTTPException(status_code=500, detail="Error communicating with AI service.")

        # Increment daily usage count upon successful invocation
        if pool:
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO user_stats (user_id, daily_requests_count, last_active_date)
                    VALUES ($1, 1, CURRENT_DATE)
                    ON CONFLICT (user_id) DO UPDATE SET
                        daily_requests_count = CASE 
                            WHEN user_stats.last_active_date = CURRENT_DATE THEN user_stats.daily_requests_count + 1
                            ELSE 1
                        END,
                        last_active_date = CURRENT_DATE;
                """, user_id)

        data = res.json()
        reply = data["choices"][0]["message"]["content"]
        return {"reply": reply}

@app.post("/api/profile/onboarding")
async def save_onboarding(profile: ProfileOnboarding, current_user: dict = Depends(get_current_user)):
    user_id = int(current_user["sub"])
    pool = await db.get_db_pool()
    if pool:
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO learner_profiles (user_id, english_level, uzbek_level, russian_level, coding_level, maths_level)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (user_id) DO UPDATE SET
                    english_level = EXCLUDED.english_level,
                    uzbek_level = EXCLUDED.uzbek_level,
                    russian_level = EXCLUDED.russian_level,
                    coding_level = EXCLUDED.coding_level,
                    maths_level = EXCLUDED.maths_level,
                    updated_at = CURRENT_TIMESTAMP;
            """, user_id, profile.english_level, profile.uzbek_level, profile.russian_level, profile.coding_level, profile.maths_level)
    return {"status": "success"}

@app.get("/api/leaderboard")
async def get_leaderboard(current_user: dict = Depends(get_current_user)):
    pool = await db.get_db_pool()
    if not pool:
        return {"streak_leaderboard": [], "xp_leaderboard": []}

    async with pool.acquire() as conn:
        streaks = await conn.fetch("""
            SELECT u.name, s.current_streak, s.total_xp 
            FROM user_stats s 
            JOIN users u ON s.user_id = u.id 
            ORDER BY s.current_streak DESC LIMIT 10
        """)
        xps = await conn.fetch("""
            SELECT u.name, s.total_xp, s.current_streak 
            FROM user_stats s 
            JOIN users u ON s.user_id = u.id 
            ORDER BY s.total_xp DESC LIMIT 10
        """)
        return {
            "streak_leaderboard": [dict(r) for r in streaks],
            "xp_leaderboard": [dict(r) for r in xps]
        }
