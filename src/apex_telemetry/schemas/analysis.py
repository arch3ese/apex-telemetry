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


class HandlingBalancePoint(BaseModel):
    """Point-by-point vehicle cornering handling balance and understeer calculation."""

    distance_m: float = Field(description="Track distance in meters")
    speed_kmh: float
    lateral_accel_g: float
    steering_angle_deg: float = Field(description="Driver steering wheel angle in degrees")
    ackerman_angle_deg: float = Field(description="Theoretical kinematic Ackerman steer angle")
    understeer_angle_deg: float = Field(
        description="Dynamic understeer angle: positive = understeer, negative = oversteer"
    )
    handling_state: str = Field(description="'understeer', 'neutral', or 'oversteer'")


class HandlingBalanceSummary(BaseModel):
    """Aggregate handling balance, understeer gradient, and cornering state distribution."""

    session_id: uuid.UUID
    wheelbase_m: float
    steering_ratio: float
    sample_count: int
    understeer_pct: float = Field(
        description="Percentage of cornering distance in understeer state"
    )
    neutral_pct: float = Field(description="Percentage of cornering distance in neutral state")
    oversteer_pct: float = Field(description="Percentage of cornering distance in oversteer state")
    max_understeer_deg: float = Field(description="Peak front-axle scrubbing angle (deg)")
    max_oversteer_deg: float = Field(description="Peak rear-axle slip / counter-steer angle (deg)")
    mean_understeer_deg: float
    points: list[HandlingBalancePoint]


class LapComparisonPoint(BaseModel):
    """Spatial distance-domain comparison point between two laps."""

    distance_m: float
    ref_speed_kmh: float
    comp_speed_kmh: float
    speed_delta_kmh: float = Field(description="comp_speed - ref_speed (positive = comp is faster)")
    cumulative_time_delta_s: float = Field(
        description="Integrated time delta in seconds (negative = comp is ahead, positive = ref is ahead)"
    )
    ref_throttle: float | None = None
    comp_throttle: float | None = None
    ref_brake: float | None = None
    comp_brake: float | None = None


class LapComparisonSummary(BaseModel):
    """Complete distance-resampled telemetry overlay and delta-time analysis between two sessions."""

    reference_session_id: uuid.UUID
    comparison_session_id: uuid.UUID
    total_distance_m: float
    final_time_delta_s: float = Field(
        description="Final gap across the lap: negative = comparison is faster, positive = reference is faster"
    )
    faster_session_id: uuid.UUID
    max_time_loss_s: float
    max_time_gain_s: float
    points: list[LapComparisonPoint]
