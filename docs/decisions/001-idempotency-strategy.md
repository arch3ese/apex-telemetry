# ADR 001: Idempotency Strategy via Database Constraints & SHA-256 Fingerprinting

## Status
Accepted

## Context
Vehicle telemetry edge loggers stream high-frequency sensor readings over cellular and trackside Wi-Fi. Due to packet loss and dead zones, HTTP requests frequently time out on the client side after the server has already committed the data. If the client retries the request, a naive backend would write duplicate records, corrupting downstream tire-slip models and energy calculations.

## Decision
We enforce idempotency at the database storage layer using a combination of:
1. **Client-Supplied `Idempotency-Key` Header:** Loggers provide a unique UUID/hash per logical batch.
2. **Database-Enforced Uniqueness:** A PostgreSQL table `ingestion_jobs` with `UNIQUE(session_id, idempotency_key)`.
3. **Deterministic SHA-256 Payload Hash:** The backend computes a SHA-256 hash of the canonical JSON readings. If the same `Idempotency-Key` is reused with a *different* payload, the system returns `409 Conflict` (tamper / accidental key collision detection).
4. **Idempotent Replay Semantics:** Upon receiving an existing key with matching payload hash, the server returns HTTP `200 OK` with the original job summary and `is_idempotent_replay: true`, executing **0 duplicate inserts**.

## Consequences
- **Pros:** 
  - 100% immune to race conditions even under concurrent retry storms.
  - Zero reliance on external caching layers (e.g. Redis) that can lose state upon restart.
  - Provides a permanent audit log of all ingestion attempts and error diagnostics.
- **Cons:** 
  - Requires an extra index and row insertion into `ingestion_jobs` per batch (negligible ~15ms overhead).
