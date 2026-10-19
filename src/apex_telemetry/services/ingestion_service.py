import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from apex_telemetry.core.exceptions import (
    BatchValidationError,
    ConcurrencyConflictError,
    EntityNotFoundError,
    IdempotencyConflictError,
)
from apex_telemetry.models import IngestionJob, TelemetryRecord, TestSession
from apex_telemetry.schemas.ingestion import IngestionJobResponse, TelemetryBatchCreate

logger = logging.getLogger(__name__)


class IngestionService:
    """Orchestrates atomic batch ingestion, idempotency guarantees, and transactional rollbacks."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def ingest_batch(
        self,
        session_id: uuid.UUID,
        idempotency_key: str,
        batch: TelemetryBatchCreate,
    ) -> IngestionJobResponse:
        """Atomically ingest a batch of telemetry records with strict idempotency."""
        # 1. Ensure target TestSession exists
        session_stmt = select(TestSession).where(TestSession.id == session_id)
        session_res = await self.db.execute(session_stmt)
        test_session = session_res.scalar_one_or_none()
        if not test_session:
            raise EntityNotFoundError(f"TestSession with ID '{session_id}' not found.")

        payload_hash = batch.compute_payload_hash()

        # 2. Check for an existing ingestion job with this idempotency key
        job_stmt = select(IngestionJob).where(
            IngestionJob.session_id == session_id,
            IngestionJob.idempotency_key == idempotency_key,
        )
        job_res = await self.db.execute(job_stmt)
        existing_job = job_res.scalar_one_or_none()

        if existing_job:
            # Detect payload tampering: same key used for different data
            if existing_job.payload_hash != payload_hash:
                raise IdempotencyConflictError(
                    f"Idempotency key '{idempotency_key}' was previously submitted with a different payload hash."
                )

            # Idempotent replay: already committed
            if existing_job.status == "committed":
                logger.info(
                    "Idempotent replay detected",
                    extra={"job_id": str(existing_job.id), "idempotency_key": idempotency_key},
                )
                return IngestionJobResponse(
                    id=existing_job.id,
                    session_id=existing_job.session_id,
                    idempotency_key=existing_job.idempotency_key,
                    status="committed",
                    record_count=existing_job.record_count,
                    payload_hash=existing_job.payload_hash,
                    error_summary=existing_job.error_summary,
                    created_at=existing_job.created_at,
                    completed_at=existing_job.completed_at,
                    is_idempotent_replay=True,
                )

            # Job is still in-flight
            if existing_job.status == "pending":
                raise ConcurrencyConflictError(
                    f"Ingestion job with key '{idempotency_key}' is currently being processed by another worker."
                )

        # 3. Execute atomic ingestion
        job_id = uuid.uuid4()
        now = datetime.now(UTC)

        new_job = IngestionJob(
            id=job_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            status="pending",
            record_count=0,
            payload_hash=payload_hash,
            created_at=now,
        )
        self.db.add(new_job)

        try:
            # Flush job record to lock in the uniqueness constraint
            await self.db.flush()

            # Prepare telemetry records
            db_records = [
                TelemetryRecord(
                    session_id=session_id,
                    ingestion_job_id=job_id,
                    channel_name=reading.channel_name,
                    recorded_at=reading.recorded_at,
                    value=reading.value,
                    unit=reading.unit,
                    quality_flag=reading.quality_flag,
                    attributes=reading.attributes,
                    created_at=now,
                )
                for reading in batch.readings
            ]
            self.db.add_all(db_records)

            # Update job state to committed
            new_job.status = "committed"
            new_job.record_count = len(db_records)
            new_job.completed_at = datetime.now(UTC)

            await self.db.commit()

            logger.info(
                "Telemetry batch committed successfully",
                extra={"job_id": str(job_id), "records_inserted": len(db_records)},
            )

            return IngestionJobResponse(
                id=new_job.id,
                session_id=new_job.session_id,
                idempotency_key=new_job.idempotency_key,
                status="committed",
                record_count=new_job.record_count,
                payload_hash=new_job.payload_hash,
                error_summary=None,
                created_at=new_job.created_at,
                completed_at=new_job.completed_at,
                is_idempotent_replay=False,
            )

        except IntegrityError as exc:
            # Transaction failed: Rollback all inserted records
            await self.db.rollback()

            error_msg = str(exc.orig) if hasattr(exc, "orig") else str(exc)

            # Check if failure was a race condition on the idempotency key
            if "uq_session_idempotency_key" in error_msg.lower():
                # Re-query the concurrent job that won the race
                concurrent_res = await self.db.execute(job_stmt)
                winner_job = concurrent_res.scalar_one_or_none()
                if (
                    winner_job
                    and winner_job.payload_hash == payload_hash
                    and winner_job.status == "committed"
                ):
                    return IngestionJobResponse(
                        id=winner_job.id,
                        session_id=winner_job.session_id,
                        idempotency_key=winner_job.idempotency_key,
                        status="committed",
                        record_count=winner_job.record_count,
                        payload_hash=winner_job.payload_hash,
                        error_summary=winner_job.error_summary,
                        created_at=winner_job.created_at,
                        completed_at=winner_job.completed_at,
                        is_idempotent_replay=True,
                    )
                raise ConcurrencyConflictError(
                    f"Concurrent write collision on idempotency key '{idempotency_key}'."
                )

            # Record failed audit job in a separate clean transaction
            failed_job = IngestionJob(
                id=job_id,
                session_id=session_id,
                idempotency_key=idempotency_key,
                status="failed",
                record_count=0,
                payload_hash=payload_hash,
                error_summary={
                    "error_type": type(exc).__name__,
                    "detail": error_msg,
                },
                completed_at=datetime.now(UTC),
            )
            self.db.add(failed_job)
            try:
                await self.db.commit()
            except SQLAlchemyError:
                await self.db.rollback()

            raise BatchValidationError(
                f"Batch ingestion rejected by database integrity constraints: {error_msg}",
                details={"job_id": str(job_id), "constraint_error": error_msg},
            )

        except Exception as exc:
            await self.db.rollback()
            logger.error("Unhandled error during telemetry ingestion", exc_info=exc)
            raise
