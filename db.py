import os
import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
pool = None

async def init_db():
    global pool
    if not DATABASE_URL:
        print("DATABASE_URL not set; database features running unattached.")
        return
    pool = await asyncpg.create_pool(dsn=DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                google_id VARCHAR(255) UNIQUE,
                email VARCHAR(255) UNIQUE NOT NULL,
                name VARCHAR(255),
                tier VARCHAR(50) DEFAULT 'free',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS learner_profiles (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                english_level VARCHAR(10),
                uzbek_level VARCHAR(10),
                russian_level VARCHAR(10),
                coding_level VARCHAR(10),
                maths_level VARCHAR(10),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                current_streak INT DEFAULT 0,
                best_streak INT DEFAULT 0,
                today_xp INT DEFAULT 0,
                total_xp INT DEFAULT 0,
                daily_requests_count INT DEFAULT 0,
                last_active_date DATE DEFAULT CURRENT_DATE
            );
        """)

async def get_db_pool():
    return pool
