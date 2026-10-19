# ApexTelemetry Architecture & System Design

ApexTelemetry is a high-performance, transactional REST platform for ingesting, validating, querying, and exporting high-frequency vehicle dynamics and CAN-bus telemetry data.

---

## 1. High-Level Component Architecture

```mermaid
flowchart TD
    subgraph Clients["Edge Loggers & Analysts"]
        A1["Automated CAN Logger (Trackside)"]
        A2["Vehicle Dynamics Engineer"]
        A3["AI / ML Training Pipeline"]
    end

    subgraph API["FastAPI Presentation Layer"]
        B1["Correlation ID Middleware (X-Request-ID)"]
        B2["Pydantic v2 Request Validation & Physics Bound Checks"]
        B3["Global Exception Handlers (RFC 7807)"]
        B4["OpenAPI / Swagger UI (/docs)"]
    end

    subgraph Services["Domain & Orchestration Services"]
        C1["IngestionService (Atomic Transactions, SHA-256 Idempotency)"]
        C2["SessionService (Session Lifecycle & Metadata)"]
        C3["TelemetryQueryService (Safe Dynamic Query Builder)"]
        C4["ExportService (Server-Side Streaming Cursor)"]
    end

    subgraph Storage["PostgreSQL 16 Engine"]
        D1[("test_sessions")]
        D2[("session_metadata")]
        D3[("ingestion_jobs (UNIQUE idempotency_key)")]
        D4[("telemetry_records (UNIQUE timestamp & CHECK constraints)")]
    end

    A1 -->|"POST /sessions/{id}/ingest + Idempotency-Key"| B1
    A2 -->|"GET /sessions/{id}/records"| B1
    A3 -->|"GET /sessions/{id}/export?format=jsonl"| B1

    B1 --> B2 --> C1
    B1 --> C2
    B1 --> C3
    B1 --> C4

    C1 -->|Atomic Commit / Rollback| D3
    C1 -->|Bulk Insert| D4
    C2 --> D1
    C2 --> D2
    C3 -->|Indexed Range Scan| D4
    C4 -->|stream_scalars() Chunked Read| D4
```

---

## 2. Relational Schema & Entity-Relationship (ER) Diagram

```mermaid
erDiagram
    test_sessions ||--o{ session_metadata : "has setup metadata"
    test_sessions ||--o{ ingestion_jobs : "audits batch submissions"
    test_sessions ||--o{ telemetry_records : "contains sensor observations"
    ingestion_jobs ||--o{ telemetry_records : "links ingested records"

    test_sessions {
        uuid id PK
        varchar session_slug UK
        varchar vehicle_id
        varchar track_name
        varchar driver_name
        varchar status
        timestamptz created_at
        timestamptz updated_at
    }

    session_metadata {
        uuid id PK
        uuid session_id FK
        varchar key
        text value
        varchar value_type
    }

    ingestion_jobs {
        uuid id PK
        uuid session_id FK
        varchar idempotency_key UK
        varchar status
        integer record_count
        varchar payload_hash
        jsonb error_summary
        timestamptz created_at
        timestamptz completed_at
    }

    telemetry_records {
        bigserial id PK
        uuid session_id FK
        uuid ingestion_job_id FK
        varchar channel_name
        timestamptz recorded_at
        double_precision value
        varchar unit
        smallint quality_flag
        jsonb attributes
        timestamptz created_at
    }
```

---

## 3. Atomic Ingestion & Concurrency Flow

```mermaid
sequenceDiagram
    autonumber
    actor Logger as Telemetry Logger
    participant API as FastAPI Ingest Router
    participant Svc as IngestionService
    participant DB as PostgreSQL 16

    Logger->>API: POST /sessions/{id}/ingest (Idempotency-Key, Batch)
    API->>Svc: ingest_batch(session_id, key, payload)
    Svc->>Svc: Compute SHA-256 payload hash
    Svc->>DB: Check existing job (session_id, key)

    alt Existing Job Found & Committed
        Svc->>Svc: Verify payload_hash matches
        Svc-->>API: Return original job summary (is_idempotent_replay: true)
        API-->>Logger: HTTP 200 OK (0 duplicate rows inserted)
    else New Ingestion Batch
        Svc->>DB: BEGIN Transaction
        Svc->>DB: INSERT INTO ingestion_jobs (status: 'pending')
        Svc->>DB: Bulk INSERT INTO telemetry_records

        alt Constraint / Physics Violation
            DB-->>Svc: IntegrityError (e.g. duplicate timestamp or check constraint)
            Svc->>DB: ROLLBACK Transaction (0 rows inserted)
            Svc->>DB: INSERT INTO ingestion_jobs (status: 'failed', error_summary)
            Svc-->>API: Raise BatchValidationError
            API-->>Logger: HTTP 422 Unprocessable Entity
        else All Records Valid
            Svc->>DB: UPDATE ingestion_jobs (status: 'committed', count: N)
            Svc->>DB: COMMIT Transaction
            Svc-->>API: Return IngestionJobResponse
            API-->>Logger: HTTP 201 Created
        end
    end
```

---

## 4. Constant Memory Streaming Flow

```mermaid
sequenceDiagram
    autonumber
    actor Client as Data Scientist / Python Script
    participant API as Export Router
    participant Stream as Async Streaming Generator
    participant DB as PostgreSQL Server-Side Cursor

    Client->>API: GET /sessions/{id}/export?format=csv
    API->>Stream: stream_export_csv(session_id, filters)
    Stream->>DB: Execute query with server-side cursor
    Stream-->>Client: HTTP 200 OK (Transfer-Encoding: chunked)
    Stream-->>Client: Yield "recorded_at,channel_name,value,unit,quality_flag\n"

    loop For each chunk in cursor (O(1) memory)
        DB-->>Stream: Fetch next 100 rows
        Stream-->>Client: Stream CSV lines over socket
    end

    Stream-->>Client: Close stream connection
```
