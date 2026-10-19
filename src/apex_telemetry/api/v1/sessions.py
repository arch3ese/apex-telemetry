import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from apex_telemetry.api.deps import get_db
from apex_telemetry.schemas.session import SessionCreate, SessionResponse
from apex_telemetry.services.session_service import SessionService

router = APIRouter(prefix="/sessions", tags=["Test Sessions"])


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new vehicle test session",
)
async def create_session(
    payload: SessionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionResponse:
    """Create a new vehicle test session with setup metadata."""
    service = SessionService(db)
    return await service.create_session(payload)


@router.get(
    "",
    response_model=list[SessionResponse],
    summary="List vehicle test sessions",
)
async def list_sessions(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    vehicle_id: Annotated[str | None, Query(description="Filter by vehicle/chassis ID")] = None,
) -> list[SessionResponse]:
    """List sessions with optional vehicle filtering."""
    service = SessionService(db)
    return await service.list_sessions(limit=limit, offset=offset, vehicle_id=vehicle_id)


@router.get(
    "/{session_id}",
    response_model=SessionResponse,
    summary="Get test session details",
)
async def get_session(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionResponse:
    """Retrieve details and metadata for a specific session."""
    service = SessionService(db)
    return await service.get_session(session_id)
