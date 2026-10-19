# ADR 002: Concurrency Control and Natural Uniqueness Constraints

## Status
Accepted

## Context
In motorsport and vehicle dyno testing, independent loggers (e.g., chassis logger vs. powertrain ECU) push telemetry concurrently. Application-level "check-then-insert" patterns (`SELECT WHERE ... IF NOT EXISTS INSERT`) suffer from race conditions: two workers checking simultaneously both see "no duplicate" and proceed to write colliding records.

## Decision
We enforce concurrency control at the PostgreSQL relational engine layer:
1. **Composite Natural Constraint:** `UNIQUE(session_id, channel_name, recorded_at)` on `telemetry_records`.
2. **Atomic Transaction Envelopes:** Batches are inserted inside an atomic `async with session.begin()` block.
3. **Rollback Guarantee:** If an `IntegrityError` is triggered by any record in a batch of 500, the transaction rolls back completely (0 rows saved), and the job is marked `failed` with exact diagnostics.

## Consequences
- **Pros:**
  - True ACID serialization guaranteed by PostgreSQL's write-ahead log (WAL) and B-tree uniqueness locks.
  - Zero risk of half-ingested or corrupted test datasets.
- **Cons:**
  - Requires handling `IntegrityError` in application code and mapping it to clean HTTP `409` or `422` error responses.
