import asyncio
from db import engine
from models import Base

async def create_tables():
    print("Creating database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Done! Database is ready.")

if __name__ == "__main__":
    asyncio.run(create_tables())
