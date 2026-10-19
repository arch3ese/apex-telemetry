import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from apex_telemetry.api.deps import get_db
from apex_telemetry.schemas.query import (
    CursorPaginationParams,
    OffsetPaginationParams,
    PaginatedResponse,
    TelemetryQueryFilter,
    TelemetryRecordResponse,
)
from apex_telemetry.services.query_service import TelemetryQueryService

router = APIRouter(prefix="/sessions/{session_id}", tags=["Telemetry Records & Exports"])


@router.get(
    "/records",
    response_model=PaginatedResponse[TelemetryRecordResponse],
    summary="Query filtered telemetry records",
)
async def query_telemetry_records(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    channel_name: Annotated[str | None, Query(description="Filter by sensor channel")] = None,
    start_time: Annotated[datetime | None, Query(description="Window start UTC")] = None,
    end_time: Annotated[datetime | None, Query(description="Window end UTC")] = None,
    min_value: Annotated[float | None, Query(description="Lower numerical bound")] = None,
    max_value: Annotated[float | None, Query(description="Upper numerical bound")] = None,
    quality_flag: Annotated[int | None, Query(ge=1, le=3, description="Quality flag")] = None,
    sort_by: Annotated[Literal["recorded_at", "value", "channel_name"], Query()] = "recorded_at",
    order: Annotated[Literal["asc", "desc"], Query()] = "asc",
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int | None, Query(ge=0)] = None,
    cursor: Annotated[str | None, Query(description="Opaque keyset pagination cursor")] = None,
) -> PaginatedResponse[TelemetryRecordResponse]:
    """Query telemetry records with dynamic filtering and offset/cursor pagination."""
    service = TelemetryQueryService(db)
    filters = TelemetryQueryFilter(
        channel_name=channel_name,
        start_time=start_time,
        end_time=end_time,
        min_value=min_value,
        max_value=max_value,
        quality_flag=quality_flag,
        sort_by=sort_by,
        order=order,
    )

    if cursor is not None:
        cursor_params = CursorPaginationParams(limit=limit, cursor=cursor)
        return await service.query_records_cursor(session_id, filters, cursor_params)

    offset_params = OffsetPaginationParams(limit=limit, offset=offset or 0)
    return await service.query_records_offset(session_id, filters, offset_params)


@router.get(
    "/export",
    summary="Stream raw telemetry dataset export",
)
async def export_telemetry_dataset(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    format: Annotated[Literal["csv", "jsonl"], Query(description="Export format")] = "csv",
    channel_name: Annotated[str | None, Query()] = None,
    start_time: Annotated[datetime | None, Query()] = None,
    end_time: Annotated[datetime | None, Query()] = None,
    min_value: Annotated[float | None, Query()] = None,
    max_value: Annotated[float | None, Query()] = None,
    quality_flag: Annotated[int | None, Query(ge=1, le=3)] = None,
) -> StreamingResponse:
    """Stream dataset export as CSV or JSONL with constant O(1) server memory consumption."""
    service = TelemetryQueryService(db)
    filters = TelemetryQueryFilter(
        channel_name=channel_name,
        start_time=start_time,
        end_time=end_time,
        min_value=min_value,
        max_value=max_value,
        quality_flag=quality_flag,
    )

    if format == "csv":
        generator = service.stream_export_csv(session_id, filters)
        media_type = "text/csv"
        filename = f"{session_id}_telemetry.csv"
    else:
        generator = service.stream_export_jsonl(session_id, filters)
        media_type = "application/x-ndjson"
        filename = f"{session_id}_telemetry.jsonl"

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(generator, media_type=media_type, headers=headers)
