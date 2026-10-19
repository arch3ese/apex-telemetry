import logging
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from apex_telemetry.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Create singleton asynchronous database engine
engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    echo=False,  # Set to True only when debugging raw SQL queries
)

# Async session factory configured not to expire objects on commit
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """Dependency yielding an async database session with automatic cleanup."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def ping_database() -> dict[str, Any]:
    """Execute a simple query to verify database connectivity and fetch engine info."""
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1 AS is_alive, version() AS db_version;"))
        row = result.mappings().one()
        return {
            "status": "healthy",
            "is_alive": row["is_alive"] == 1,
            "version": row["db_version"],
        }
