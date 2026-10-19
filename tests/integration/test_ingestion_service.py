import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apex_telemetry.core.exceptions import BatchValidationError, IdempotencyConflictError
from apex_telemetry.models import IngestionJob, TelemetryRecord, TestSession
from apex_telemetry.schemas.ingestion import TelemetryBatchCreate, TelemetryReadingCreate
from apex_telemetry.services.ingestion_service import IngestionService


async def _create_test_session(db: AsyncSession) -> TestSession:
    session = TestSession(
        id=uuid.uuid4(),
        session_slug=f"ingest-test-{uuid.uuid4().hex[:8]}",
        vehicle_id="CHASSIS-INGEST",
        track_name="Bahrain International Circuit",
    )
    db.add(session)
    await db.commit()
    return session


@pytest.mark.asyncio
async def test_successful_atomic_batch_ingest(db_session: AsyncSession):
    session = await _create_test_session(db_session)
    service = IngestionService(db_session)

    readings = [
        TelemetryReadingCreate(
            channel_name="wheel_speed_fl",
            recorded_at=datetime(2026, 10, 19, 10, 0, i, tzinfo=UTC),
            value=100.0 + i,
            unit="km/h",
            quality_flag=1,
        )
        for i in range(10)
    ]
    batch = TelemetryBatchCreate(readings=readings)
    key = f"key-{uuid.uuid4().hex}"

    response = await service.ingest_batch(session.id, key, batch)

    assert response.status == "committed"
    assert response.record_count == 10
    assert response.is_idempotent_replay is False

    # Verify rows in PostgreSQL
    stmt = select(TelemetryRecord).where(TelemetryRecord.session_id == session.id)
    records = (await db_session.execute(stmt)).scalars().all()
    assert len(records) == 10


@pytest.mark.asyncio
async def test_idempotent_retry_returns_original_without_duplicate_inserts(
    db_session: AsyncSession,
):
    session = await _create_test_session(db_session)
    service = IngestionService(db_session)

    readings = [
        TelemetryReadingCreate(
            channel_name="yaw_rate_deg_s",
            recorded_at=datetime(2026, 10, 19, 10, 5, i, tzinfo=UTC),
            value=5.0 + i,
            unit="deg/s",
        )
        for i in range(5)
    ]
    batch = TelemetryBatchCreate(readings=readings)
    key = f"idemp-test-{uuid.uuid4().hex}"

    # First attempt: commits new batch
    resp1 = await service.ingest_batch(session.id, key, batch)
    assert resp1.status == "committed"
    assert resp1.is_idempotent_replay is False

    # Second attempt: exact same payload and key (simulating network retry)
    resp2 = await service.ingest_batch(session.id, key, batch)
    assert resp2.status == "committed"
    assert resp2.id == resp1.id
    assert resp2.is_idempotent_replay is True

    # Database must still have only 5 rows, NOT 10
    stmt = select(TelemetryRecord).where(TelemetryRecord.session_id == session.id)
    records = (await db_session.execute(stmt)).scalars().all()
    assert len(records) == 5


@pytest.mark.asyncio
async def test_tampered_payload_with_same_key_rejected(db_session: AsyncSession):
    session = await _create_test_session(db_session)
    service = IngestionService(db_session)
    key = f"tamper-key-{uuid.uuid4().hex}"

    batch1 = TelemetryBatchCreate(
        readings=[
            TelemetryReadingCreate(
                channel_name="brake_pressure_bar",
                recorded_at=datetime(2026, 10, 19, 11, 0, 0, tzinfo=UTC),
                value=45.0,
                unit="bar",
            )
        ]
    )
    await service.ingest_batch(session.id, key, batch1)

    # Attempt to submit different data with the exact same key
    batch2 = TelemetryBatchCreate(
        readings=[
            TelemetryReadingCreate(
                channel_name="brake_pressure_bar",
                recorded_at=datetime(2026, 10, 19, 11, 0, 0, tzinfo=UTC),
                value=80.0,  # Changed value -> different hash
                unit="bar",
            )
        ]
    )

    with pytest.raises(IdempotencyConflictError) as exc:
        await service.ingest_batch(session.id, key, batch2)

    assert "different payload hash" in str(exc.value)


@pytest.mark.asyncio
async def test_batch_rollback_on_database_constraint_failure(db_session: AsyncSession):
    session = await _create_test_session(db_session)
    session_id = session.id
    service = IngestionService(db_session)

    fixed_time = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)

    # 1. First commit a baseline record
    seed_batch = TelemetryBatchCreate(
        readings=[
            TelemetryReadingCreate(
                channel_name="lateral_accel_g",
                recorded_at=fixed_time,
                value=1.5,
                unit="g",
            )
        ]
    )
    await service.ingest_batch(session_id, "baseline-key", seed_batch)

    # 2. Prepare a new batch where record #1 is novel, but record #2 collides on (session_id, channel_name, recorded_at)
    colliding_batch = TelemetryBatchCreate(
        readings=[
            TelemetryReadingCreate(
                channel_name="lateral_accel_g",
                recorded_at=datetime(2026, 10, 19, 12, 0, 1, tzinfo=UTC),
                value=1.6,
                unit="g",
            ),
            TelemetryReadingCreate(
                channel_name="lateral_accel_g",
                recorded_at=fixed_time,  # Collision with baseline!
                value=1.9,
                unit="g",
            ),
        ]
    )

    with pytest.raises(BatchValidationError) as exc:
        await service.ingest_batch(session_id, "failing-batch-key", colliding_batch)

    assert "integrity constraints" in str(exc.value).lower()

    # 3. CRUCIAL TEST: Verify atomic rollback.
    # The novel record (12:00:01) must NOT be in the database. Total count must still be 1!
    stmt = select(TelemetryRecord).where(TelemetryRecord.session_id == session_id)
    records = (await db_session.execute(stmt)).scalars().all()
    assert len(records) == 1
    assert records[0].recorded_at == fixed_time

    # 4. IngestionJob audit record must be recorded as 'failed'
    job_stmt = select(IngestionJob).where(
        IngestionJob.session_id == session_id,
        IngestionJob.idempotency_key == "failing-batch-key",
    )
    failed_job = (await db_session.execute(job_stmt)).scalar_one()
    assert failed_job.status == "failed"
    assert failed_job.error_summary is not None


@pytest.mark.asyncio
async def test_concurrent_duplicate_ingestion_race_condition(session_factory):
    """Verify concurrent requests with the identical idempotency key resolve safely."""
    # Create session using an independent connection
    async with session_factory() as setup_db:
        session = await _create_test_session(setup_db)
        session_id = session.id

    batch = TelemetryBatchCreate(
        readings=[
            TelemetryReadingCreate(
                channel_name="steering_angle_deg",
                recorded_at=datetime(2026, 10, 19, 14, 0, i, tzinfo=UTC),
                value=-15.0 + i,
                unit="deg",
            )
            for i in range(5)
        ]
    )
    race_key = f"race-{uuid.uuid4().hex}"

    async def worker():
        async with session_factory() as worker_db:
            service = IngestionService(worker_db)
            return await service.ingest_batch(session_id, race_key, batch)

    # Launch two workers simultaneously on separate connections
    res1, res2 = await asyncio.gather(worker(), worker())

    assert res1.status == "committed"
    assert res2.status == "committed"
    assert res1.id == res2.id

    # Exactly one was the original commit, the other was the idempotent replay
    replays = [res1.is_idempotent_replay, res2.is_idempotent_replay]
    assert True in replays
    assert False in replays

    # Verify database has exactly 5 rows (no duplicate records from race)
    async with session_factory() as verify_db:
        stmt = select(TelemetryRecord).where(TelemetryRecord.session_id == session_id)
        records = (await verify_db.execute(stmt)).scalars().all()
        assert len(records) == 5
