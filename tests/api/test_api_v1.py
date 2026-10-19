import json
import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check_endpoint(api_client: AsyncClient):
    response = await api_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"]["is_alive"] is True


@pytest.mark.asyncio
async def test_correlation_id_propagation(api_client: AsyncClient):
    custom_cid = "trace-uuid-abcdef-12345"
    response = await api_client.get("/health", headers={"X-Request-ID": custom_cid})
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == custom_cid


@pytest.mark.asyncio
async def test_session_lifecycle_and_conflict(api_client: AsyncClient):
    slug = f"api-test-{uuid.uuid4().hex[:8]}"
    payload = {
        "session_slug": slug,
        "vehicle_id": "CHASSIS-API-01",
        "track_name": "Circuit de Spa-Francorchamps",
        "driver_name": "L. Hamilton",
        "metadata": [
            {"key": "tire_compound", "value": "Soft", "value_type": "string"},
            {"key": "ambient_temp_c", "value": "24.5", "value_type": "number"},
        ],
    }

    # 1. Create Session
    create_res = await api_client.post("/api/v1/sessions", json=payload)
    assert create_res.status_code == 201
    created_session = create_res.json()
    session_id = created_session["id"]
    assert created_session["session_slug"] == slug
    assert len(created_session["metadata_entries"]) == 2

    # 2. Get Session by ID
    get_res = await api_client.get(f"/api/v1/sessions/{session_id}")
    assert get_res.status_code == 200
    assert get_res.json()["track_name"] == "Circuit de Spa-Francorchamps"

    # 3. Attempt duplicate slug -> HTTP 409 Conflict
    dup_res = await api_client.post("/api/v1/sessions", json=payload)
    assert dup_res.status_code == 409
    assert "already exists" in dup_res.json()["detail"]


@pytest.mark.asyncio
async def test_batch_ingestion_http_status_codes(api_client: AsyncClient):
    # Register session
    slug = f"ingest-api-{uuid.uuid4().hex[:8]}"
    sess_res = await api_client.post(
        "/api/v1/sessions",
        json={
            "session_slug": slug,
            "vehicle_id": "CHASSIS-API-02",
            "track_name": "Monza",
        },
    )
    session_id = sess_res.json()["id"]

    key = f"key-{uuid.uuid4().hex}"
    valid_batch = {
        "readings": [
            {
                "channel_name": "lateral_accel_g",
                "recorded_at": datetime.now(UTC).isoformat(),
                "value": 2.1,
                "unit": "g",
                "quality_flag": 1,
            }
        ]
    }

    # 1. Missing Idempotency-Key header -> HTTP 422
    no_key_res = await api_client.post(f"/api/v1/sessions/{session_id}/ingest", json=valid_batch)
    assert no_key_res.status_code == 422

    # 2. Initial Batch Ingest -> HTTP 201 Created
    first_res = await api_client.post(
        f"/api/v1/sessions/{session_id}/ingest",
        json=valid_batch,
        headers={"Idempotency-Key": key},
    )
    assert first_res.status_code == 201
    assert first_res.json()["status"] == "committed"
    assert first_res.json()["is_idempotent_replay"] is False

    # 3. Idempotent Replay -> HTTP 200 OK
    replay_res = await api_client.post(
        f"/api/v1/sessions/{session_id}/ingest",
        json=valid_batch,
        headers={"Idempotency-Key": key},
    )
    assert replay_res.status_code == 200
    assert replay_res.json()["status"] == "committed"
    assert replay_res.json()["is_idempotent_replay"] is True

    # 4. Tampered Payload with same key -> HTTP 409 Conflict
    tampered_batch = {
        "readings": [
            {
                "channel_name": "lateral_accel_g",
                "recorded_at": datetime.now(UTC).isoformat(),
                "value": 3.5,  # modified value
                "unit": "g",
                "quality_flag": 1,
            }
        ]
    }
    conflict_res = await api_client.post(
        f"/api/v1/sessions/{session_id}/ingest",
        json=tampered_batch,
        headers={"Idempotency-Key": key},
    )
    assert conflict_res.status_code == 409
    assert "different payload hash" in conflict_res.json()["detail"]

    # 5. Physical Plausibility Violation (brake pressure = 500 bar) -> HTTP 422 Unprocessable
    invalid_batch = {
        "readings": [
            {
                "channel_name": "brake_pressure_bar",
                "recorded_at": datetime.now(UTC).isoformat(),
                "value": 500.0,  # Impossible physical pressure
                "unit": "bar",
            }
        ]
    }
    phys_res = await api_client.post(
        f"/api/v1/sessions/{session_id}/ingest",
        json=invalid_batch,
        headers={"Idempotency-Key": "key-bad-physics"},
    )
    assert phys_res.status_code == 422


@pytest.mark.asyncio
async def test_streaming_export_api(api_client: AsyncClient):
    slug = f"export-api-{uuid.uuid4().hex[:8]}"
    sess_res = await api_client.post(
        "/api/v1/sessions",
        json={
            "session_slug": slug,
            "vehicle_id": "CHASSIS-API-EXPORT",
            "track_name": "Silverstone",
        },
    )
    session_id = sess_res.json()["id"]

    batch = {
        "readings": [
            {
                "channel_name": "wheel_speed_fl",
                "recorded_at": datetime(2026, 10, 19, 14, 0, i, tzinfo=UTC).isoformat(),
                "value": 150.0 + i,
                "unit": "km/h",
            }
            for i in range(5)
        ]
    }
    await api_client.post(
        f"/api/v1/sessions/{session_id}/ingest",
        json=batch,
        headers={"Idempotency-Key": f"key-export-{slug}"},
    )

    # 1. CSV Export
    csv_res = await api_client.get(f"/api/v1/sessions/{session_id}/export?format=csv")
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers.get("content-type", "")
    csv_lines = csv_res.text.strip().split("\n")
    assert csv_lines[0].startswith("recorded_at,channel_name")
    assert len(csv_lines) == 6  # header + 5 rows

    # 2. JSONL Export
    jsonl_res = await api_client.get(f"/api/v1/sessions/{session_id}/export?format=jsonl")
    assert jsonl_res.status_code == 200
    jsonl_lines = jsonl_res.text.strip().split("\n")
    assert len(jsonl_lines) == 5
    for line in jsonl_lines:
        item = json.loads(line)
        assert item["channel_name"] == "wheel_speed_fl"
