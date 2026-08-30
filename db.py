"""
Async database layer (SQLite by default via aiosqlite; point DATABASE_URL at
Postgres — e.g. postgresql+asyncpg://... — with zero code changes when you
outgrow SQLite).

Two tables:
- users            one row per Google account, holds the current tier
- usage_counters   one row per (user, day), used to enforce daily quotas
                    and to survive server restarts (unlike an in-memory count)
"""

from __future__ import annotations

import datetime as dt
import os
from typing import AsyncGenerator, Optional

from sqlalchemy import Date, ForeignKey, Integer, String, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./nexora.db")

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    picture: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    tier: Mapped[str] = mapped_column(String(20), default="free")
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=dt.datetime.utcnow)


class UsageCounter(Base):
    __tablename__ = "usage_counters"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_usage_user_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    count: Mapped[int] = mapped_column(Integer, default=0)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — one session per request, closed automatically."""
    async with SessionLocal() as session:
        yield session


async def get_or_create_user(
    session: AsyncSession, google_sub: str, email: str, name: str, picture: Optional[str]
) -> User:
    result = await session.execute(select(User).where(User.google_sub == google_sub))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(google_sub=google_sub, email=email, name=name, picture=picture, tier="free")
        session.add(user)
        await session.commit()
        await session.refresh(user)
    elif user.name != name or user.picture != picture:
        # Keep the cached profile fields fresh on every login.
        user.name, user.picture = name, picture
        await session.commit()
    return user


async def get_user_by_id(session: AsyncSession, user_id: int) -> Optional[User]:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_stripe_customer(session: AsyncSession, customer_id: str) -> Optional[User]:
    result = await session.execute(select(User).where(User.stripe_customer_id == customer_id))
    return result.scalar_one_or_none()


async def get_today_usage(session: AsyncSession, user_id: int) -> int:
    today = dt.date.today()
    result = await session.execute(
        select(UsageCounter).where(UsageCounter.user_id == user_id, UsageCounter.day == today)
    )
    row = result.scalar_one_or_none()
    return row.count if row else 0


async def increment_usage(session: AsyncSession, user_id: int) -> int:
    """Atomically bumps today's counter for a user and returns the new total."""
    today = dt.date.today()
    result = await session.execute(
        select(UsageCounter).where(UsageCounter.user_id == user_id, UsageCounter.day == today)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = UsageCounter(user_id=user_id, day=today, count=1)
        session.add(row)
        await session.commit()
        return 1
    row.count += 1
    await session.commit()
    return row.count
