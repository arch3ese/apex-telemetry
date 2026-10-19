import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apex_telemetry.api.deps import get_db
from apex_telemetry.core.exceptions import EntityNotFoundError
from apex_telemetry.models import IngestionJob
from apex_telemetry.schemas.ingestion import IngestionJobResponse, TelemetryBatchCreate
from apex_telemetry.services.ingestion_service import IngestionService

router = APIRouter(tags=["Telemetry Ingestion"])


@router.post(
    "/sessions/{session_id}/ingest",
    response_model=IngestionJobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Batch ingest telemetry readings",
)
async def ingest_telemetry_batch(
    session_id: uuid.UUID,
    payload: TelemetryBatchCreate,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=128,
            description="Client-generated unique token to guarantee exactly-once processing",
        ),
    ],
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> IngestionJobResponse:
    """Atomically ingest a batch of telemetry sensor readings.

    - Returns HTTP 201 Created for the initial successful batch commit.
    - Returns HTTP 200 OK for idempotent replays (retries with identical key and payload).
    - Returns HTTP 409 Conflict if the key is reused with a different payload.
    - Returns HTTP 422 Unprocessable Entity if physical bounds are violated.
    """
    service = IngestionService(db)
    result = await service.ingest_batch(session_id, idempotency_key, payload)

    if result.is_idempotent_replay:
        response.status_code = status.HTTP_200_OK

    return result


@router.get(
    "/ingestion-jobs/{job_id}",
    response_model=IngestionJobResponse,
    summary="Get ingestion job audit status",
)
async def get_ingestion_job(
    job_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> IngestionJobResponse:
    """Retrieve audit status and error diagnostics for an ingestion job."""
    stmt = select(IngestionJob).where(IngestionJob.id == job_id)
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()
    if not job:
        raise EntityNotFoundError(f"IngestionJob with ID '{job_id}' not found.")

    return IngestionJobResponse.model_validate(job)
