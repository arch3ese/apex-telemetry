import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apex_telemetry.models import IngestionJob, TelemetryRecord, TestSession


@pytest.mark.asyncio
async def test_quality_flag_check_constraint_enforced(db_session: AsyncSession):
    """PostgreSQL CHECK constraint 'ck_telemetry_quality_flag' must reject quality_flag not in (1, 2, 3)."""
    # 1. Create a parent test session
    session = TestSession(
        id=uuid.uuid4(),
        session_slug=f"check-test-{uuid.uuid4().hex[:8]}",
        vehicle_id="CHASSIS-001",
        track_name="Silverstone GP",
    )
    db_session.add(session)
    await db_session.flush()

    # 2. Create parent ingestion job
    job = IngestionJob(
        id=uuid.uuid4(),
        session_id=session.id,
        idempotency_key=f"job-{uuid.uuid4().hex[:8]}",
        status="pending",
        payload_hash="dummy_hash_123",
    )
    db_session.add(job)
    await db_session.flush()

    # 3. Attempt to insert record with quality_flag = 4 (illegal value)
    bad_record = TelemetryRecord(
        session_id=session.id,
        ingestion_job_id=job.id,
        channel_name="lateral_accel_g",
        recorded_at=datetime.now(UTC),
        value=1.42,
        unit="g",
        quality_flag=4,  # Violates CHECK (quality_flag IN (1, 2, 3))
    )
    db_session.add(bad_record)

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()

    assert (
        "ck_telemetry_quality_flag" in str(exc_info.value).lower()
        or "check" in str(exc_info.value).lower()
    )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_duplicate_channel_timestamp_rejected(db_session: AsyncSession):
    """PostgreSQL UNIQUE constraint 'uq_session_channel_timestamp' must reject duplicate readings."""
    session = TestSession(
        id=uuid.uuid4(),
        session_slug=f"unique-test-{uuid.uuid4().hex[:8]}",
        vehicle_id="CHASSIS-002",
        track_name="Nürburgring Nordschleife",
    )
    db_session.add(session)
    await db_session.flush()

    job = IngestionJob(
        id=uuid.uuid4(),
        session_id=session.id,
        idempotency_key=f"job-{uuid.uuid4().hex[:8]}",
        status="pending",
        payload_hash="dummy_hash_456",
    )
    db_session.add(job)
    await db_session.flush()

    timestamp = datetime.now(UTC)

    # First record: valid insert
    record1 = TelemetryRecord(
        session_id=session.id,
        ingestion_job_id=job.id,
        channel_name="yaw_rate_deg_s",
        recorded_at=timestamp,
        value=12.5,
        unit="deg/s",
        quality_flag=1,
    )
    db_session.add(record1)
    await db_session.flush()

    # Second record: conflicting duplicate timestamp for the same session and channel
    record2 = TelemetryRecord(
        session_id=session.id,
        ingestion_job_id=job.id,
        channel_name="yaw_rate_deg_s",
        recorded_at=timestamp,  # Colliding timestamp
        value=14.2,
        unit="deg/s",
        quality_flag=1,
    )
    db_session.add(record2)

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()

    assert (
        "uq_session_channel_timestamp" in str(exc_info.value).lower()
        or "unique" in str(exc_info.value).lower()
    )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_idempotency_key_unique_per_session(db_session: AsyncSession):
    """PostgreSQL UNIQUE constraint 'uq_session_idempotency_key' must reject duplicate job keys."""
    session = TestSession(
        id=uuid.uuid4(),
        session_slug=f"idemp-test-{uuid.uuid4().hex[:8]}",
        vehicle_id="CHASSIS-003",
        track_name="Spa-Francorchamps",
    )
    db_session.add(session)
    await db_session.flush()

    key = "batch-run-fixed-uuid"

    job1 = IngestionJob(
        id=uuid.uuid4(),
        session_id=session.id,
        idempotency_key=key,
        status="committed",
        payload_hash="hash_alpha",
    )
    db_session.add(job1)
    await db_session.flush()

    job2 = IngestionJob(
        id=uuid.uuid4(),
        session_id=session.id,
        idempotency_key=key,  # Duplicate key for same session
        status="pending",
        payload_hash="hash_beta",
    )
    db_session.add(job2)

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()

    assert (
        "uq_session_idempotency_key" in str(exc_info.value).lower()
        or "unique" in str(exc_info.value).lower()
    )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_cascade_delete_session(db_session: AsyncSession):
    """Deleting a TestSession must cascade and delete its metadata and records."""
    from sqlalchemy import select

    session = TestSession(
        id=uuid.uuid4(),
        session_slug=f"cascade-test-{uuid.uuid4().hex[:8]}",
        vehicle_id="CHASSIS-CASCADE",
        track_name="Monza",
    )
    db_session.add(session)
    await db_session.flush()

    job = IngestionJob(
        id=uuid.uuid4(),
        session_id=session.id,
        idempotency_key=f"job-{uuid.uuid4().hex[:8]}",
        status="committed",
        payload_hash="hash_cascade",
    )
    db_session.add(job)
    await db_session.flush()

    record = TelemetryRecord(
        session_id=session.id,
        ingestion_job_id=job.id,
        channel_name="brake_pressure_bar",
        recorded_at=datetime.now(UTC),
        value=85.0,
        unit="bar",
        quality_flag=1,
    )
    db_session.add(record)
    await db_session.flush()

    # Verify record exists
    result = await db_session.execute(
        select(TelemetryRecord).where(TelemetryRecord.session_id == session.id)
    )
    assert len(result.scalars().all()) == 1

    # Delete parent session
    await db_session.delete(session)
    await db_session.flush()

    # Verify telemetry records cascaded and were wiped
    result_after = await db_session.execute(
        select(TelemetryRecord).where(TelemetryRecord.session_id == session.id)
    )
    assert len(result_after.scalars().all()) == 0
