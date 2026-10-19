from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apex_telemetry.db.session import get_db_session


async def get_db() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency yielding an async database session."""
    async for session in get_db_session():
        yield session


SessionDep = Annotated = Depends(get_db)
