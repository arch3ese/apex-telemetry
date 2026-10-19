from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class TelemetryQueryFilter(BaseModel):
    """Parameters for dynamically filtering telemetry time-series slices."""

    model_config = ConfigDict(extra="forbid")

    channel_name: Annotated[
        str | None,
        Field(
            default=None,
            pattern=r"^[a-z0-9_]+$",
            description="Filter by specific sensor channel (e.g. wheel_speed_fl)",
        ),
    ]
    start_time: Annotated[
        datetime | None,
        Field(default=None, description="Start of observation time window (inclusive UTC)"),
    ]
    end_time: Annotated[
        datetime | None,
        Field(default=None, description="End of observation time window (inclusive UTC)"),
    ]
    min_value: Annotated[
        float | None, Field(default=None, description="Minimum sensor value bound")
    ]
    max_value: Annotated[
        float | None, Field(default=None, description="Maximum sensor value bound")
    ]
    quality_flag: Annotated[
        int | None,
        Field(
            default=None,
            ge=1,
            le=3,
            description="Filter by quality flag (1=Valid, 2=Suspect, 3=Bad)",
        ),
    ]

    # Sorting
    sort_by: Annotated[
        Literal["recorded_at", "value", "channel_name"],
        Field(default="recorded_at", description="Field to order results by"),
    ]
    order: Annotated[
        Literal["asc", "desc"],
        Field(default="asc", description="Sort order direction"),
    ]


class OffsetPaginationParams(BaseModel):
    """Standard limit-offset pagination parameters."""

    model_config = ConfigDict(extra="forbid")

    limit: Annotated[int, Field(default=100, ge=1, le=1000, description="Items per page")]
    offset: Annotated[int, Field(default=0, ge=0, description="Number of items to skip")]


class CursorPaginationParams(BaseModel):
    """Keyset / cursor pagination parameters for high-throughput time-series traversal."""

    model_config = ConfigDict(extra="forbid")

    limit: Annotated[int, Field(default=100, ge=1, le=1000, description="Items per page")]
    cursor: Annotated[
        str | None,
        Field(
            default=None,
            description="Opaque base64 cursor pointing to the last item of the previous page",
        ),
    ]


class TelemetryRecordResponse(BaseModel):
    """Response model for a single telemetry record."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: str
    channel_name: str
    recorded_at: datetime
    value: float
    unit: str
    quality_flag: int
    attributes: dict | None = None


class PaginatedResponse[T](BaseModel):
    """Generic envelope for paginated results."""

    items: list[T]
    total_count: int | None = None
    next_cursor: str | None = None
    has_more: bool
