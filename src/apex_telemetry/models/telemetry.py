import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apex_telemetry.db.base import Base


class TestSession(Base):
    """Represents a physical vehicle testing run (e.g. track test, dyno calibration)."""

    __test__ = False
    __tablename__ = "test_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    session_slug: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        comment="Human-readable unique identifier, e.g. silverstone-lap-01",
    )
    vehicle_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="Chassis VIN or prototype identifier, e.g. PROTOTYPE-GT-03",
    )
    track_name: Mapped[str] = mapped_column(String(128), nullable=False)
    driver_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="active",
        comment="Session lifecycle: active, completed, or archived",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    metadata_entries: Mapped[list["SessionMetadata"]] = relationship(
        "SessionMetadata",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    ingestion_jobs: Mapped[list["IngestionJob"]] = relationship(
        "IngestionJob",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    telemetry_records: Mapped[list["TelemetryRecord"]] = relationship(
        "TelemetryRecord",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="raise",
    )


class SessionMetadata(Base):
    """Key-value attributes describing test conditions (tire type, ambient temp, setup)."""

    __tablename__ = "session_metadata"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    value_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="string",
        comment="Value data type: string, number, boolean, json",
    )

    __table_args__ = (UniqueConstraint("session_id", "key", name="uq_session_metadata_key"),)

    # Relationships
    session: Mapped["TestSession"] = relationship("TestSession", back_populates="metadata_entries")


class IngestionJob(Base):
    """Tracks the lifecycle, audit status, and idempotency of a multi-record telemetry batch."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="Client-supplied unique token to guarantee exactly-once processing",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        comment="Status: pending, committed, failed",
    )
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="SHA-256 fingerprint of the ingested batch payload",
    )
    error_summary: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Diagnostics JSON if validation or constraint violation occurred",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("session_id", "idempotency_key", name="uq_session_idempotency_key"),
    )

    # Relationships
    session: Mapped["TestSession"] = relationship("TestSession", back_populates="ingestion_jobs")
    records: Mapped[list["TelemetryRecord"]] = relationship(
        "TelemetryRecord",
        back_populates="ingestion_job",
        lazy="raise",
    )


class TelemetryRecord(Base):
    """High-frequency sensor observation from a CAN-bus or telemetry channel."""

    __tablename__ = "telemetry_records"

    id: Mapped[int] = mapped_column(
        Integer().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    ingestion_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    channel_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Sensor channel name, e.g. wheel_speed_fl, lateral_accel_g, brake_pressure_bar",
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="High-precision sensor timestamp",
    )
    value: Mapped[float] = mapped_column(
        DOUBLE_PRECISION,
        nullable=False,
        comment="Numerical reading in physical engineering units",
    )
    unit: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="Unit symbol, e.g. km/h, g, deg/s, bar",
    )
    quality_flag: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=1,
        comment="Data quality: 1 = Valid/Good, 2 = Suspect/Degraded, 3 = Fault/Bad",
    )
    attributes: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Optional diagnostic attributes, e.g. calibration offsets or raw ADC counts",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # Natural composite uniqueness: a sensor channel cannot have two readings at the same timestamp
        UniqueConstraint(
            "session_id",
            "channel_name",
            "recorded_at",
            name="uq_session_channel_timestamp",
        ),
        # Quality flag validation domain
        CheckConstraint("quality_flag IN (1, 2, 3)", name="ck_telemetry_quality_flag"),
        # Compound B-Tree indexes for fast time-slice queries
        Index("idx_telemetry_session_time", "session_id", recorded_at.desc()),
        Index(
            "idx_telemetry_session_channel_time", "session_id", "channel_name", recorded_at.desc()
        ),
    )

    # Relationships
    session: Mapped["TestSession"] = relationship("TestSession", back_populates="telemetry_records")
    ingestion_job: Mapped["IngestionJob"] = relationship("IngestionJob", back_populates="records")
