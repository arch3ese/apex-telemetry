import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class SessionMetadataCreate(BaseModel):
    """Key-value metadata attribute submitted during session creation."""

    key: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")]
    value: str
    value_type: Annotated[str, Field(default="string", pattern=r"^(string|number|boolean|json)$")]


class SessionCreate(BaseModel):
    """Payload to register a new vehicle test session."""

    model_config = ConfigDict(extra="forbid")

    session_slug: Annotated[
        str,
        Field(
            min_length=3,
            max_length=64,
            pattern=r"^[a-z0-9-]+$",
            description="URL-safe unique identifier, e.g. silverstone-lap-01",
        ),
    ]
    vehicle_id: Annotated[
        str,
        Field(
            min_length=2,
            max_length=64,
            description="Vehicle chassis or prototype VIN, e.g. PROTOTYPE-GT-03",
        ),
    ]
    track_name: Annotated[str, Field(min_length=2, max_length=128)]
    driver_name: Annotated[str | None, Field(default=None, max_length=128)]
    metadata: list[SessionMetadataCreate] = Field(default_factory=list)


class SessionResponse(BaseModel):
    """Public representation of a vehicle test session."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_slug: str
    vehicle_id: str
    track_name: str
    driver_name: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    metadata_entries: list[SessionMetadataCreate] = Field(default_factory=list)
