from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from apex_telemetry.config import get_settings

settings = get_settings()

# Use NullPool in test environment to ensure each test gets an isolated fresh connection
test_engine = create_async_engine(
    settings.DATABASE_URL,
    poolclass=NullPool,
    echo=False,
)

test_session_factory = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@pytest.fixture
def session_factory():
    """Provide the async session factory for tests that manage their own session lifecycle."""
    return test_session_factory


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    """Provide a real PostgreSQL async session for integration tests, rolling back on completion."""
    async with test_session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()
