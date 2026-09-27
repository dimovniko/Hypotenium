from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings
from .models import Base

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session():
    async with async_session() as session:
        yield session


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        # Мини-миграции: create_all не добавляет колонки в существующие таблицы
        await conn.execute(
            text("ALTER TABLE files ADD COLUMN IF NOT EXISTS comment TEXT NOT NULL DEFAULT ''")
        )
        await conn.execute(
            text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS context TEXT NOT NULL DEFAULT ''")
        )
        await conn.execute(
            text("ALTER TABLE chats ADD COLUMN IF NOT EXISTS mode VARCHAR(32) NOT NULL DEFAULT 'normal'")
        )
        await conn.execute(text("ALTER TABLE chats ADD COLUMN IF NOT EXISTS tool_state JSONB"))
        await conn.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS actions JSONB"))
