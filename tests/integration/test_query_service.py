import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apex_telemetry.models import TestSession
from apex_telemetry.schemas.ingestion import TelemetryBatchCreate, TelemetryReadingCreate
from apex_telemetry.schemas.query import (
    CursorPaginationParams,
    OffsetPaginationParams,
    TelemetryQueryFilter,
)
from apex_telemetry.services.ingestion_service import IngestionService
from apex_telemetry.services.query_service import TelemetryQueryService


async def _seed_test_session_with_data(db: AsyncSession, count: int = 30) -> uuid.UUID:
    session = TestSession(
        id=uuid.uuid4(),
        session_slug=f"query-test-{uuid.uuid4().hex[:8]}",
        vehicle_id="CHASSIS-QUERY",
        track_name="Suzuka Circuit",
    )
    db.add(session)
    await db.commit()

    ingest_svc = IngestionService(db)
    readings = []
    for i in range(count):
        # alternate channels
        channel = "wheel_speed_fl" if i % 2 == 0 else "lateral_accel_g"
        val = 100.0 + i if i % 2 == 0 else 0.5 + (i * 0.1)
        unit = "km/h" if i % 2 == 0 else "g"

        readings.append(
            TelemetryReadingCreate(
                channel_name=channel,
                recorded_at=datetime(2026, 10, 19, 12, 0, i, tzinfo=UTC),
                value=round(val, 2),
                unit=unit,
                quality_flag=1 if i < 25 else 2,
            )
        )

    batch = TelemetryBatchCreate(readings=readings)
    await ingest_svc.ingest_batch(session.id, f"seed-batch-{uuid.uuid4().hex}", batch)
    return session.id


@pytest.mark.asyncio
async def test_dynamic_filtering_by_channel_and_time_window(db_session: AsyncSession):
    session_id = await _seed_test_session_with_data(db_session, count=20)
    query_svc = TelemetryQueryService(db_session)

    # Filter only wheel_speed_fl between seconds 4 and 14
    filters = TelemetryQueryFilter(
        channel_name="wheel_speed_fl",
        start_time=datetime(2026, 10, 19, 12, 0, 4, tzinfo=UTC),
        end_time=datetime(2026, 10, 19, 12, 0, 14, tzinfo=UTC),
        sort_by="recorded_at",
        order="asc",
    )
    pagination = OffsetPaginationParams(limit=50, offset=0)

    res = await query_svc.query_records_offset(session_id, filters, pagination)

    assert res.total_count is not None
    assert len(res.items) > 0
    for item in res.items:
        assert item.channel_name == "wheel_speed_fl"
        assert item.recorded_at >= filters.start_time
        assert item.recorded_at <= filters.end_time


@pytest.mark.asyncio
async def test_numeric_bounds_filtering(db_session: AsyncSession):
    session_id = await _seed_test_session_with_data(db_session, count=20)
    query_svc = TelemetryQueryService(db_session)

    filters = TelemetryQueryFilter(
        channel_name="wheel_speed_fl",
        min_value=105.0,
        max_value=115.0,
    )
    pagination = OffsetPaginationParams(limit=50, offset=0)

    res = await query_svc.query_records_offset(session_id, filters, pagination)

    for item in res.items:
        assert 105.0 <= item.value <= 115.0


@pytest.mark.asyncio
async def test_offset_pagination_consistency(db_session: AsyncSession):
    session_id = await _seed_test_session_with_data(db_session, count=25)
    query_svc = TelemetryQueryService(db_session)
    filters = TelemetryQueryFilter()

    # Page 1: 10 items
    p1 = await query_svc.query_records_offset(
        session_id, filters, OffsetPaginationParams(limit=10, offset=0)
    )
    # Page 2: 10 items
    p2 = await query_svc.query_records_offset(
        session_id, filters, OffsetPaginationParams(limit=10, offset=10)
    )
    # Page 3: 5 items
    p3 = await query_svc.query_records_offset(
        session_id, filters, OffsetPaginationParams(limit=10, offset=20)
    )

    assert len(p1.items) == 10
    assert len(p2.items) == 10
    assert len(p3.items) == 5
    assert p1.total_count == 25

    all_ids = [r.id for r in p1.items] + [r.id for r in p2.items] + [r.id for r in p3.items]
    # No duplicate IDs across offset boundaries
    assert len(set(all_ids)) == 25


@pytest.mark.asyncio
async def test_keyset_cursor_pagination_traversal(db_session: AsyncSession):
    session_id = await _seed_test_session_with_data(db_session, count=30)
    query_svc = TelemetryQueryService(db_session)
    filters = TelemetryQueryFilter(order="asc")

    traversed_ids = []
    cursor = None

    while True:
        params = CursorPaginationParams(limit=8, cursor=cursor)
        page = await query_svc.query_records_cursor(session_id, filters, params)

        traversed_ids.extend([r.id for r in page.items])
        if not page.has_more:
            break
        cursor = page.next_cursor

    # All 30 records reached via cursor with zero skips and zero duplicates
    assert len(traversed_ids) == 30
    assert len(set(traversed_ids)) == 30


@pytest.mark.asyncio
async def test_streaming_csv_export(db_session: AsyncSession):
    session_id = await _seed_test_session_with_data(db_session, count=10)
    query_svc = TelemetryQueryService(db_session)
    filters = TelemetryQueryFilter()

    chunks = []
    async for chunk in query_svc.stream_export_csv(session_id, filters):
        chunks.append(chunk)

    full_csv = "".join(chunks)
    lines = full_csv.strip().split("\n")

    # Line 0 is header
    assert lines[0] == "recorded_at,channel_name,value,unit,quality_flag,attributes"
    # 10 data rows
    assert len(lines) == 11


@pytest.mark.asyncio
async def test_streaming_jsonl_export(db_session: AsyncSession):
    session_id = await _seed_test_session_with_data(db_session, count=10)
    query_svc = TelemetryQueryService(db_session)
    filters = TelemetryQueryFilter()

    jsonl_lines = []
    async for chunk in query_svc.stream_export_jsonl(session_id, filters):
        jsonl_lines.append(chunk.strip())

    assert len(jsonl_lines) == 10
    for line in jsonl_lines:
        parsed = json.loads(line)
        assert "recorded_at" in parsed
        assert "channel_name" in parsed
        assert "value" in parsed
        assert parsed["session_id"] == str(session_id)
