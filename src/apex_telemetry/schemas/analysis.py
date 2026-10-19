import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class TractionCirclePoint(BaseModel):
    """Point representation on a G-G friction circle diagram."""

    recorded_at: datetime
    lateral_accel_g: float = Field(description="Lateral acceleration in g (positive = right turn)")
    longitudinal_accel_g: float = Field(
        description="Longitudinal acceleration in g (positive = throttle, negative = braking)"
    )
    combined_accel_g: float = Field(
        description="Total resultant acceleration demand: sqrt(a_lat^2 + a_long^2)"
    )
    speed_kmh: float | None = Field(default=None, description="Vehicle speed at sample point")


class TractionCircleSummary(BaseModel):
    """Aggregate vehicle dynamics traction envelope and grip utilization metrics."""

    session_id: uuid.UUID
    sample_count: int
    peak_lateral_g: float = Field(description="Maximum absolute lateral cornering grip (g)")
    peak_braking_g: float = Field(description="Maximum longitudinal deceleration under braking (g)")
    peak_longitudinal_accel_g: float = Field(
        description="Maximum longitudinal acceleration under throttle (g)"
    )
    peak_combined_g: float = Field(description="Maximum resultant vector in G-G plane (g)")
    p95_combined_g: float = Field(description="95th percentile sustained combined grip (g)")
    high_utilization_pct: float = Field(
        description="Percentage of driving time where vehicle operates at >= 80% of peak grip"
    )
    points: list[TractionCirclePoint] = Field(
        description="Sampled points for plotting the G-G traction circle"
    )


class BrakingCornerEvent(BaseModel):
    """Identified braking zone and corner apex entry/exit phase."""

    event_index: int
    brake_initiation_time: datetime
    initial_speed_kmh: float
    apex_time: datetime
    apex_min_speed_kmh: float
    peak_deceleration_g: float
    peak_brake_pressure_bar: float | None = None
    speed_loss_kmh: float


class BrakingPerformanceSummary(BaseModel):
    """Braking performance and corner apex speed dynamics analysis."""

    session_id: uuid.UUID
    event_count: int
    mean_apex_speed_kmh: float
    max_deceleration_g: float
    events: list[BrakingCornerEvent]


class GearShiftEvent(BaseModel):
    """Powertrain transmission shift event detecting engine powerband transition."""

    from_gear: int
    to_gear: int
    timestamp: datetime
    pre_shift_rpm: float
    post_shift_rpm: float
    rpm_drop: float
    vehicle_speed_kmh: float


class PowertrainShiftSummary(BaseModel):
    """Powertrain transmission analysis: gear ratio drops and shift point efficiency."""

    session_id: uuid.UUID
    total_upshifts: int
    mean_shift_rpm: float
    mean_rpm_drop: float
    gear_ratios_rpm_per_kmh: dict[int, float] = Field(
        description="Average engine RPM per unit km/h for each active gear ratio"
    )
    shifts: list[GearShiftEvent]
