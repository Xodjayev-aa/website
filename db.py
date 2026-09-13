"""
Database engine/session setup for Nexora Academy.

Reads DATABASE_URL from the environment. Expects a Postgres URL using the
asyncpg driver, e.g.:

    postgresql+asyncpg://user:password@host:5432/dbname

If you're given a plain "postgresql://..." URL (common with managed DB
providers), this module will automatically rewrite it to use the asyncpg
driver so you don't have to edit it by hand.
"""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://localhost/nexora")

# Normalize common URL forms to the asyncpg driver string SQLAlchemy expects.
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_db():
    """FastAPI dependency that yields a DB session and always closes it."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
