from datetime import date
from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy.types import Integer, String, Date

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    # Matches the user ID from your auth system (e.g., Clerk, Supabase, or custom auth)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    
    # Defaults to the "free" tier defined in tiers.py
    tier: Mapped[str] = mapped_column(String, default="free")
    
    # Daily tracking 
    last_message_date: Mapped[date] = mapped_column(Date, default=date.today)
    daily_message_count: Mapped[int] = mapped_column(Integer, default=0)
