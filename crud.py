from datetime import date
from sqlalchemy.ext.asyncio import AsyncSession
from models import User

async def get_today_usage(session: AsyncSession, user_id: str) -> int:
    user = await session.get(User, user_id)
    
    if not user:
        return 0
        
    # If their last message was before today, their count for today is 0
    if user.last_message_date != date.today():
        return 0
        
    return user.daily_message_count

async def increment_usage(session: AsyncSession, user_id: str) -> int:
    user = await session.get(User, user_id)
    
    if not user:
        # First time seeing this user: create their record
        user = User(id=user_id, last_message_date=date.today(), daily_message_count=1)
        session.add(user)
        await session.commit()
        return 1

    # If it's a new day, reset the count. Otherwise, add 1.
    if user.last_message_date != date.today():
        user.last_message_date = date.today()
        user.daily_message_count = 1
    else:
        user.daily_message_count += 1

    await session.commit()
    return user.daily_message_count
