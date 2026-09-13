from datetime import date, datetime

from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy.types import Integer, String, Date, DateTime

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    # Internal primary key (UUID string). Distinct from the Google subject id
    # so we're never locked into one auth provider's id format.
    id: Mapped[str] = mapped_column(String, primary_key=True)

    # Google OAuth identity
    google_sub: Mapped[str] = mapped_column(String, unique=True, index=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    picture: Mapped[str] = mapped_column(String, default="")

    # Defaults to the "free" tier defined in subscription.py
    tier: Mapped[str] = mapped_column(String, default="free")

    # Stripe
    stripe_customer_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    stripe_subscription_id: Mapped[str] = mapped_column(String, nullable=True)

    # Daily usage tracking
    last_message_date: Mapped[date] = mapped_column(Date, default=date.today)
    daily_message_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
