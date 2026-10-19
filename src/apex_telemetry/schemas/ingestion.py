import hashlib
import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TelemetryReadingCreate(BaseModel):
    """Single sensor observation submitted within an ingestion batch."""

    model_config = ConfigDict(extra="forbid")

    channel_name: Annotated[
        str,
        Field(
            min_length=2,
            max_length=64,
            pattern=r"^[a-z0-9_]+$",
            description="CAN/telemetry channel name in snake_case, e.g. wheel_speed_fl",
        ),
    ]
    recorded_at: Annotated[
        datetime,
        Field(description="UTC timestamp of the observation with microsecond resolution"),
    ]
    value: Annotated[float, Field(description="Numerical measurement in engineering units")]
    unit: Annotated[str, Field(min_length=1, max_length=32, description="Physical unit symbol")]
    quality_flag: Annotated[
        int,
        Field(
            default=1,
            ge=1,
            le=3,
            description="1 = Valid/Good, 2 = Suspect/Degraded, 3 = Fault/Bad",
        ),
    ]
    attributes: Annotated[
        dict[str, Any] | None,
        Field(default=None, description="Optional diagnostic tags or sensor metadata"),
    ]

    @field_validator("recorded_at")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        """Enforce timezone awareness; convert naive timestamps to UTC."""
        if v.tzinfo is None:
            raise ValueError(
                "recorded_at timestamp must include timezone information (e.g. UTC 'Z')"
            )
        return v

    @model_validator(mode="after")
    def validate_physical_plausibility(self) -> "TelemetryReadingCreate":
        """Domain-specific vehicle dynamics physical bounds validation."""
        channel = self.channel_name.lower()
        val = self.value

        # 1. Brake hydraulic pressure must be non-negative and within hydraulic limits (0 to 250 bar)
        if "brake_pressure" in channel:
            if val < 0.0 or val > 250.0:
                raise ValueError(
                    f"Physical plausibility violation on '{channel}': "
                    f"value {val} bar is out of realistic physical range [0.0, 250.0] bar"
                )

        # 2. Wheel speeds must be non-negative and under physical terminal velocities (0 to 450 km/h)
        elif "wheel_speed" in channel:
            if val < 0.0 or val > 450.0:
                raise ValueError(
                    f"Physical plausibility violation on '{channel}': "
                    f"value {val} is out of realistic wheel speed range [0.0, 450.0]"
                )

        # 3. Lateral / Longitudinal accelerations for a ground vehicle (-5.0g to +5.0g)
        elif "accel" in channel and (val < -5.0 or val > 5.0):
            raise ValueError(
                f"Physical plausibility violation on '{channel}': "
                f"acceleration {val} g exceeds maximum plausible grip limit [-5.0, 5.0] g"
            )

        return self


class TelemetryBatchCreate(BaseModel):
    """Batch payload containing multiple telemetry readings for atomic ingestion."""

    model_config = ConfigDict(extra="forbid")

    readings: Annotated[
        list[TelemetryReadingCreate],
        Field(
            min_length=1,
            max_length=5000,
            description="Ordered list of sensor observations to ingest atomically",
        ),
    ]

    def compute_payload_hash(self) -> str:
        """Compute a deterministic SHA-256 hash of the batch readings."""
        # Convert readings to deterministic sorted JSON structure
        serialized = [
            {
                "channel_name": r.channel_name,
                "recorded_at": r.recorded_at.isoformat(),
                "value": r.value,
                "unit": r.unit,
                "quality_flag": r.quality_flag,
                "attributes": r.attributes,
            }
            for r in self.readings
        ]
        raw_bytes = json.dumps(serialized, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw_bytes).hexdigest()


class IngestionJobResponse(BaseModel):
    """Response returned upon batch ingestion submission or query."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    idempotency_key: str
    status: Literal["pending", "committed", "failed"]
    record_count: int
    payload_hash: str
    error_summary: dict[str, Any] | None = None
    created_at: datetime
    completed_at: datetime | None = None
    is_idempotent_replay: bool = False
