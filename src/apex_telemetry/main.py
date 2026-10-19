import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from apex_telemetry.api.v1.router import api_v1_router
from apex_telemetry.config import get_settings
from apex_telemetry.core.exceptions import (
    BatchValidationError,
    ConcurrencyConflictError,
    EntityNotFoundError,
    IdempotencyConflictError,
)
from apex_telemetry.core.logging import correlation_id_ctx, setup_logging
from apex_telemetry.db.session import engine, ping_database

settings = get_settings()
setup_logging(log_level=settings.LOG_LEVEL, json_format=(settings.ENVIRONMENT != "development"))
logger = logging.getLogger("apex_telemetry.app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Application startup and graceful shutdown lifecycle."""
    logger.info("Initializing ApexTelemetry application", extra={"env": settings.ENVIRONMENT})
    yield
    logger.info("Disposing database connection pool on shutdown...")
    await engine.dispose()
    logger.info("Shutdown complete.")


app = FastAPI(
    title=settings.PROJECT_NAME,
    description=(
        "High-performance REST API for ingesting, validating, querying, and exporting "
        "high-frequency vehicle dynamics and CAN-bus telemetry datasets under strict "
        "concurrency and physical plausibility constraints."
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next) -> Response:
    """Inject and propagate X-Request-ID correlation headers for end-to-end tracing."""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    token = correlation_id_ctx.set(request_id)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        correlation_id_ctx.reset(token)


# --- Global Exception Handlers ---


from typing import Annotated

from fastapi import Depends
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from apex_telemetry.api.deps import get_db


def _error_response(
    status_code: int, title: str, detail: str, extra: dict[str, Any] | None = None
) -> JSONResponse:
    content: dict[str, Any] = {
        "status": status_code,
        "title": title,
        "detail": detail,
        "correlation_id": correlation_id_ctx.get(),
    }
    if extra:
        content["details"] = jsonable_encoder(extra)
    return JSONResponse(status_code=status_code, content=content)


@app.exception_handler(EntityNotFoundError)
async def entity_not_found_handler(request: Request, exc: EntityNotFoundError) -> JSONResponse:
    return _error_response(status.HTTP_404_NOT_FOUND, "Not Found", exc.message, exc.details)


@app.exception_handler(IdempotencyConflictError)
async def idempotency_conflict_handler(
    request: Request, exc: IdempotencyConflictError
) -> JSONResponse:
    return _error_response(
        status.HTTP_409_CONFLICT, "Idempotency Conflict", exc.message, exc.details
    )


@app.exception_handler(ConcurrencyConflictError)
async def concurrency_conflict_handler(
    request: Request, exc: ConcurrencyConflictError
) -> JSONResponse:
    return _error_response(status.HTTP_409_CONFLICT, "Conflict", exc.message, exc.details)


@app.exception_handler(BatchValidationError)
async def batch_validation_handler(request: Request, exc: BatchValidationError) -> JSONResponse:
    return _error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "Validation Error", exc.message, exc.details
    )


@app.exception_handler(RequestValidationError)
async def pydantic_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return _error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "Unprocessable Content",
        "Request body or parameters failed validation schema.",
        {"errors": exc.errors()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled server exception", exc_info=exc)
    return _error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "Internal Server Error",
        "An unexpected error occurred. Please reference the correlation_id for support.",
    )


# --- Health & Routers ---


@app.get("/health", tags=["System Health"], summary="Liveness & Database Probe")
async def health_check(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Probe verifying application liveness and database connectivity."""
    db_status = await ping_database(db)
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
        "database": db_status,
    }


app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)
