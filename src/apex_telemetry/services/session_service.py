import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apex_telemetry.core.exceptions import ConcurrencyConflictError, EntityNotFoundError
from apex_telemetry.models import SessionMetadata, TestSession
from apex_telemetry.schemas.session import SessionCreate, SessionMetadataCreate, SessionResponse


class SessionService:
    """Handles vehicle test session lifecycle and associated metadata."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_session(self, data: SessionCreate) -> SessionResponse:
        """Create a new vehicle test session with initial setup metadata."""
        session_id = uuid.uuid4()
        session = TestSession(
            id=session_id,
            session_slug=data.session_slug,
            vehicle_id=data.vehicle_id,
            track_name=data.track_name,
            driver_name=data.driver_name,
            status="active",
        )
        self.db.add(session)

        for meta in data.metadata:
            meta_record = SessionMetadata(
                session_id=session_id,
                key=meta.key,
                value=meta.value,
                value_type=meta.value_type,
            )
            self.db.add(meta_record)

        try:
            await self.db.commit()
            await self.db.refresh(session, ["metadata_entries"])
        except IntegrityError as exc:
            await self.db.rollback()
            raise ConcurrencyConflictError(
                f"TestSession with slug '{data.session_slug}' already exists."
            ) from exc

        return self._to_response(session)

    async def get_session(self, session_id: uuid.UUID) -> SessionResponse:
        """Retrieve a specific session by its UUID."""
        stmt = (
            select(TestSession)
            .where(TestSession.id == session_id)
            .options(selectinload(TestSession.metadata_entries))
        )
        result = await self.db.execute(stmt)
        session = result.scalar_one_or_none()
        if not session:
            raise EntityNotFoundError(f"TestSession with ID '{session_id}' not found.")
        return self._to_response(session)

    async def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        vehicle_id: str | None = None,
    ) -> list[SessionResponse]:
        """List sessions with optional vehicle filtering."""
        stmt = (
            select(TestSession)
            .options(selectinload(TestSession.metadata_entries))
            .order_by(TestSession.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if vehicle_id:
            stmt = stmt.where(TestSession.vehicle_id == vehicle_id)

        result = await self.db.execute(stmt)
        sessions = result.scalars().all()
        return [self._to_response(s) for s in sessions]

    def _to_response(self, session: TestSession) -> SessionResponse:
        meta_items = [
            SessionMetadataCreate(
                key=m.key,
                value=m.value,
                value_type=m.value_type,
            )
            for m in session.metadata_entries
        ]
        return SessionResponse(
            id=session.id,
            session_slug=session.session_slug,
            vehicle_id=session.vehicle_id,
            track_name=session.track_name,
            driver_name=session.driver_name,
            status=session.status,
            created_at=session.created_at,
            updated_at=session.updated_at,
            metadata_entries=meta_items,
        )
