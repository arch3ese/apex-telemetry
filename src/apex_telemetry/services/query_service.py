import base64
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apex_telemetry.core.exceptions import EntityNotFoundError
from apex_telemetry.models import TelemetryRecord, TestSession
from apex_telemetry.schemas.query import (
    CursorPaginationParams,
    OffsetPaginationParams,
    PaginatedResponse,
    TelemetryQueryFilter,
    TelemetryRecordResponse,
)


class TelemetryQueryService:
    """Handles parameterized querying, offset & cursor pagination, and streaming exports."""

    def __init__(self, db: AsyncSession):
        self.db = db

    def _build_base_query(self, session_id: uuid.UUID, filters: TelemetryQueryFilter) -> Select:
        """Construct parameterized SQLAlchemy query applying dynamic filter criteria."""
        query = select(TelemetryRecord).where(TelemetryRecord.session_id == session_id)

        if filters.channel_name:
            query = query.where(TelemetryRecord.channel_name == filters.channel_name)
        if filters.start_time:
            query = query.where(TelemetryRecord.recorded_at >= filters.start_time)
        if filters.end_time:
            query = query.where(TelemetryRecord.recorded_at <= filters.end_time)
        if filters.min_value is not None:
            query = query.where(TelemetryRecord.value >= filters.min_value)
        if filters.max_value is not None:
            query = query.where(TelemetryRecord.value <= filters.max_value)
        if filters.quality_flag is not None:
            query = query.where(TelemetryRecord.quality_flag == filters.quality_flag)

        # Apply safe parameterized sort ordering
        sort_column_map = {
            "recorded_at": TelemetryRecord.recorded_at,
            "value": TelemetryRecord.value,
            "channel_name": TelemetryRecord.channel_name,
        }
        sort_col = sort_column_map[filters.sort_by]
        direction = sort_col.desc() if filters.order == "desc" else sort_col.asc()
        id_direction = (
            TelemetryRecord.id.desc() if filters.order == "desc" else TelemetryRecord.id.asc()
        )

        # Secondary sort by ID guarantees deterministic ordering for pagination
        return query.order_by(direction, id_direction)

    async def _verify_session_exists(self, session_id: uuid.UUID) -> None:
        stmt = select(TestSession.id).where(TestSession.id == session_id)
        res = await self.db.execute(stmt)
        if not res.scalar_one_or_none():
            raise EntityNotFoundError(f"TestSession with ID '{session_id}' not found.")

    async def query_records_offset(
        self,
        session_id: uuid.UUID,
        filters: TelemetryQueryFilter,
        pagination: OffsetPaginationParams,
    ) -> PaginatedResponse[TelemetryRecordResponse]:
        """Query telemetry records using standard limit/offset pagination."""
        await self._verify_session_exists(session_id)
        base_query = self._build_base_query(session_id, filters)

        # Count total matching rows
        count_stmt = select(func.count()).select_from(base_query.subquery())
        total_count = (await self.db.execute(count_stmt)).scalar() or 0

        # Execute paginated slice
        paginated_stmt = base_query.limit(pagination.limit).offset(pagination.offset)
        result = await self.db.execute(paginated_stmt)
        records = result.scalars().all()

        items = [
            TelemetryRecordResponse(
                id=r.id,
                session_id=str(r.session_id),
                channel_name=r.channel_name,
                recorded_at=r.recorded_at,
                value=r.value,
                unit=r.unit,
                quality_flag=r.quality_flag,
                attributes=r.attributes,
            )
            for r in records
        ]

        has_more = (pagination.offset + len(items)) < total_count
        return PaginatedResponse(
            items=items,
            total_count=total_count,
            has_more=has_more,
        )

    async def query_records_cursor(
        self,
        session_id: uuid.UUID,
        filters: TelemetryQueryFilter,
        pagination: CursorPaginationParams,
    ) -> PaginatedResponse[TelemetryRecordResponse]:
        """Query telemetry records using keyset / cursor-based pagination."""
        await self._verify_session_exists(session_id)
        query = select(TelemetryRecord).where(TelemetryRecord.session_id == session_id)

        if filters.channel_name:
            query = query.where(TelemetryRecord.channel_name == filters.channel_name)
        if filters.start_time:
            query = query.where(TelemetryRecord.recorded_at >= filters.start_time)
        if filters.end_time:
            query = query.where(TelemetryRecord.recorded_at <= filters.end_time)
        if filters.min_value is not None:
            query = query.where(TelemetryRecord.value >= filters.min_value)
        if filters.max_value is not None:
            query = query.where(TelemetryRecord.value <= filters.max_value)
        if filters.quality_flag is not None:
            query = query.where(TelemetryRecord.quality_flag == filters.quality_flag)

        # Decode cursor if supplied
        if pagination.cursor:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(pagination.cursor.encode()).decode())
                cursor_ts = datetime.fromisoformat(decoded["recorded_at"])
                cursor_id = int(decoded["id"])

                if filters.order == "asc":
                    query = query.where(
                        (TelemetryRecord.recorded_at > cursor_ts)
                        | (
                            (TelemetryRecord.recorded_at == cursor_ts)
                            & (TelemetryRecord.id > cursor_id)
                        )
                    )
                else:
                    query = query.where(
                        (TelemetryRecord.recorded_at < cursor_ts)
                        | (
                            (TelemetryRecord.recorded_at == cursor_ts)
                            & (TelemetryRecord.id < cursor_id)
                        )
                    )
            except Exception as exc:
                raise ValueError(f"Invalid pagination cursor: {exc}") from exc

        # Set sort order
        if filters.order == "asc":
            query = query.order_by(TelemetryRecord.recorded_at.asc(), TelemetryRecord.id.asc())
        else:
            query = query.order_by(TelemetryRecord.recorded_at.desc(), TelemetryRecord.id.desc())

        # Request limit + 1 to determine if subsequent records exist
        fetch_limit = pagination.limit + 1
        query = query.limit(fetch_limit)

        result = await self.db.execute(query)
        rows = list(result.scalars().all())

        has_more = len(rows) > pagination.limit
        page_records = rows[: pagination.limit]

        next_cursor = None
        if has_more and page_records:
            last = page_records[-1]
            cursor_data = {
                "recorded_at": last.recorded_at.isoformat(),
                "id": last.id,
            }
            next_cursor = base64.urlsafe_b64encode(json.dumps(cursor_data).encode()).decode()

        items = [
            TelemetryRecordResponse(
                id=r.id,
                session_id=str(r.session_id),
                channel_name=r.channel_name,
                recorded_at=r.recorded_at,
                value=r.value,
                unit=r.unit,
                quality_flag=r.quality_flag,
                attributes=r.attributes,
            )
            for r in page_records
        ]

        return PaginatedResponse(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def stream_export_csv(
        self,
        session_id: uuid.UUID,
        filters: TelemetryQueryFilter,
    ) -> AsyncGenerator[str]:
        """Stream telemetry records as CSV chunks with O(1) server memory consumption."""
        await self._verify_session_exists(session_id)
        query = self._build_base_query(session_id, filters)

        # Yield CSV header
        yield "recorded_at,channel_name,value,unit,quality_flag,attributes\n"

        # Stream records using server-side cursor execution
        stream = await self.db.stream_scalars(query)
        async for record in stream:
            attrs = json.dumps(record.attributes) if record.attributes else ""
            line = f"{record.recorded_at.isoformat()},{record.channel_name},{record.value},{record.unit},{record.quality_flag},{attrs}\n"
            yield line

    async def stream_export_jsonl(
        self,
        session_id: uuid.UUID,
        filters: TelemetryQueryFilter,
    ) -> AsyncGenerator[str]:
        """Stream telemetry records as newline-delimited JSON (JSONL)."""
        await self._verify_session_exists(session_id)
        query = self._build_base_query(session_id, filters)

        stream = await self.db.stream_scalars(query)
        async for record in stream:
            entry: dict[str, Any] = {
                "id": record.id,
                "session_id": str(record.session_id),
                "channel_name": record.channel_name,
                "recorded_at": record.recorded_at.isoformat(),
                "value": record.value,
                "unit": record.unit,
                "quality_flag": record.quality_flag,
                "attributes": record.attributes,
            }
            yield json.dumps(entry) + "\n"
