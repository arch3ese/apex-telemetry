# ADR 003: Constant-Memory O(1) Streaming Export Architecture

## Status
Accepted

## Context
Downstream data scientists, vehicle dynamics analysts, and AI evaluation teams frequently export full test session slices containing 100,000+ observations. Traditional APIs load all rows into an in-memory list (`scalars().all()`), serialize them to a massive string, and return it. This causes severe memory spikes, server out-of-memory (OOM) crashes, and high latency.

## Decision
We implement a chunked streaming export pipeline:
1. **Server-Side Streaming Cursors:** SQLAlchemy's `stream_scalars()` fetches rows in small database cursor chunks directly from PostgreSQL.
2. **Asynchronous Generators:** Python `async for record in stream: yield chunk` pipes formatted rows directly to the HTTP socket.
3. **Dual Formats:** Supports both RFC-compliant CSV with headers and NDJSON (JSON Lines—the standard for ML training pipelines).

## Consequences
- **Pros:**
  - Flat $O(1)$ RAM usage on the API worker regardless of dataset size (10 rows vs. 1,000,000 rows consume the same ~10MB buffer).
  - Time-to-first-byte (TTFB) drops to $< 50\,\text{ms}$ since the client starts receiving bytes immediately.
- **Cons:**
  - Database connection remains checked out for the duration of the HTTP download stream.
