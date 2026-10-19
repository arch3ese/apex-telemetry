# ApexTelemetry: High-Frequency Vehicle Dynamics & CAN-Bus Ingestion Platform

A high-performance, transactional REST API designed to ingest, validate, store, query, and export high-frequency vehicle dynamics and CAN-bus telemetry data under strict physical plausibility constraints and concurrency guarantees.

## Problem Statement
During track testing, dyno calibration, and autonomous vehicle runs, multichannel loggers stream high-frequency sensor readings (wheel speeds, longitudinal/lateral accelerations, yaw rate, steering angles, brake line pressures). Unreliable track networks cause duplicate batch retries, sensor glitches introduce physically impossible values, and concurrent uploads risk data contamination.

ApexTelemetry guarantees:
- **Idempotent batch ingestion:** Client retries with identical `Idempotency-Key` headers never create duplicate rows.
- **Atomic transactions:** Complete multi-record rollback if even one reading fails validation or schema constraints.
- **Physical plausibility checks:** Multi-tier validation via Pydantic schemas and PostgreSQL check constraints.
- **Memory-safe streaming exports:** Chunked streaming of hundreds of thousands of records without server out-of-memory errors.

## Tech Stack
- **Language:** Python 3.13
- **Framework:** FastAPI
- **Database:** PostgreSQL 16 (via Docker Compose)
- **Data Access:** SQLAlchemy 2.0 (Async with `asyncpg` + `psycopg3` for migrations)
- **Migrations:** Alembic
- **Validation:** Pydantic v2
- **Testing:** pytest, pytest-asyncio, httpx
