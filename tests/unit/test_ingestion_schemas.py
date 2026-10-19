from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from apex_telemetry.schemas.ingestion import TelemetryBatchCreate, TelemetryReadingCreate


def test_valid_telemetry_reading():
    reading = TelemetryReadingCreate(
        channel_name="lateral_accel_g",
        recorded_at=datetime.now(UTC),
        value=1.85,
        unit="g",
        quality_flag=1,
    )
    assert reading.channel_name == "lateral_accel_g"
    assert reading.value == 1.85


def test_physical_plausibility_brake_pressure():
    # Negative pressure should fail
    with pytest.raises(ValidationError) as exc:
        TelemetryReadingCreate(
            channel_name="brake_pressure_front",
            recorded_at=datetime.now(UTC),
            value=-5.0,
            unit="bar",
        )
    assert "out of realistic physical range" in str(exc.value)

    # Exceeding hydraulic maximum (250 bar) should fail
    with pytest.raises(ValidationError) as exc:
        TelemetryReadingCreate(
            channel_name="brake_pressure_front",
            recorded_at=datetime.now(UTC),
            value=320.0,
            unit="bar",
        )
    assert "out of realistic physical range" in str(exc.value)


def test_physical_plausibility_wheel_speed():
    # Negative wheel speed should fail
    with pytest.raises(ValidationError) as exc:
        TelemetryReadingCreate(
            channel_name="wheel_speed_fl",
            recorded_at=datetime.now(UTC),
            value=-12.0,
            unit="km/h",
        )
    assert "out of realistic wheel speed range" in str(exc.value)

    # Impossible speed (>450 km/h) should fail
    with pytest.raises(ValidationError) as exc:
        TelemetryReadingCreate(
            channel_name="wheel_speed_fl",
            recorded_at=datetime.now(UTC),
            value=600.0,
            unit="km/h",
        )
    assert "out of realistic wheel speed range" in str(exc.value)


def test_physical_plausibility_acceleration():
    # Exceeding maximum physical tire grip limits (-5g to +5g)
    with pytest.raises(ValidationError) as exc:
        TelemetryReadingCreate(
            channel_name="lateral_accel_g",
            recorded_at=datetime.now(UTC),
            value=7.2,
            unit="g",
        )
    assert "exceeds maximum plausible grip limit" in str(exc.value)


def test_deterministic_payload_hashing():
    ts = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)
    batch1 = TelemetryBatchCreate(
        readings=[
            TelemetryReadingCreate(
                channel_name="wheel_speed_fl",
                recorded_at=ts,
                value=120.0,
                unit="km/h",
            ),
            TelemetryReadingCreate(
                channel_name="yaw_rate_deg_s",
                recorded_at=ts,
                value=15.0,
                unit="deg/s",
            ),
        ]
    )

    batch2 = TelemetryBatchCreate(
        readings=[
            TelemetryReadingCreate(
                channel_name="wheel_speed_fl",
                recorded_at=ts,
                value=120.0,
                unit="km/h",
            ),
            TelemetryReadingCreate(
                channel_name="yaw_rate_deg_s",
                recorded_at=ts,
                value=15.0,
                unit="deg/s",
            ),
        ]
    )

    # Identical content must yield identical SHA-256 fingerprint
    assert batch1.compute_payload_hash() == batch2.compute_payload_hash()

    # Modified content must yield a different hash
    batch3 = TelemetryBatchCreate(
        readings=[
            TelemetryReadingCreate(
                channel_name="wheel_speed_fl",
                recorded_at=ts,
                value=120.5,  # modified
                unit="km/h",
            ),
        ]
    )
    assert batch1.compute_payload_hash() != batch3.compute_payload_hash()
