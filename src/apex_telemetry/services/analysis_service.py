import math
import uuid
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apex_telemetry.core.exceptions import EntityNotFoundError
from apex_telemetry.models.telemetry import TelemetryRecord, TestSession
from apex_telemetry.schemas.analysis import (
    BrakingCornerEvent,
    BrakingPerformanceSummary,
    GearShiftEvent,
    PowertrainShiftSummary,
    TractionCirclePoint,
    TractionCircleSummary,
)


class VehicleDynamicsAnalysisService:
    """Computes motorsport vehicle dynamics, tire grip utilization, and powertrain metrics."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _verify_session_exists(self, session_id: uuid.UUID) -> None:
        stmt = select(TestSession.id).where(TestSession.id == session_id)
        res = await self.db.execute(stmt)
        if not res.scalar_one_or_none():
            raise EntityNotFoundError(f"TestSession {session_id} does not exist.")

    async def compute_traction_circle(
        self, session_id: uuid.UUID, max_points: int = 1000
    ) -> TractionCircleSummary:
        """Compute the G-G friction circle diagram and tire grip utilization envelope."""
        await self._verify_session_exists(session_id)

        # Retrieve acceleration and speed channels
        stmt = (
            select(
                TelemetryRecord.recorded_at,
                TelemetryRecord.channel_name,
                TelemetryRecord.value,
            )
            .where(
                TelemetryRecord.session_id == session_id,
                TelemetryRecord.channel_name.in_(
                    [
                        "lateral_accel_g",
                        "longitudinal_accel_g",
                        "LateralAccel",
                        "LongitudinalAccel",
                        "speed_kmh",
                        "Speed",
                    ]
                ),
            )
            .order_by(TelemetryRecord.recorded_at.asc())
        )
        res = await self.db.execute(stmt)
        rows = res.all()

        # Group readings by recorded_at timestamp
        time_slices: dict[Any, dict[str, float]] = {}
        for recorded_at, channel_name, val in rows:
            # Normalize channel names
            norm_name = channel_name.lower()
            if "lat" in norm_name:
                key = "lat"
            elif "long" in norm_name:
                key = "long"
            elif "speed" in norm_name:
                key = "speed"
            else:
                continue

            if recorded_at not in time_slices:
                time_slices[recorded_at] = {}
            time_slices[recorded_at][key] = float(val)

        points: list[TractionCirclePoint] = []
        combined_list: list[float] = []

        for ts, data in time_slices.items():
            if "lat" in data and "long" in data:
                lat = data["lat"]
                long = data["long"]
                comb = math.sqrt(lat * lat + long * long)
                combined_list.append(comb)
                points.append(
                    TractionCirclePoint(
                        recorded_at=ts,
                        lateral_accel_g=round(lat, 4),
                        longitudinal_accel_g=round(long, 4),
                        combined_accel_g=round(comb, 4),
                        speed_kmh=round(data["speed"], 2) if "speed" in data else None,
                    )
                )

        if not points:
            return TractionCircleSummary(
                session_id=session_id,
                sample_count=0,
                peak_lateral_g=0.0,
                peak_braking_g=0.0,
                peak_longitudinal_accel_g=0.0,
                peak_combined_g=0.0,
                p95_combined_g=0.0,
                high_utilization_pct=0.0,
                points=[],
            )

        lat_arr = np.array([p.lateral_accel_g for p in points])
        long_arr = np.array([p.longitudinal_accel_g for p in points])
        comb_arr = np.array(combined_list)

        peak_lat = float(np.max(np.abs(lat_arr)))
        braking_vals = long_arr[long_arr < 0]
        peak_brake = float(np.max(np.abs(braking_vals))) if len(braking_vals) > 0 else 0.0
        accel_vals = long_arr[long_arr > 0]
        peak_accel = float(np.max(accel_vals)) if len(accel_vals) > 0 else 0.0
        peak_comb = float(np.max(comb_arr))
        p95_comb = float(np.percentile(comb_arr, 95))

        # High utilization: fraction of operating samples >= 80% of p95 grip
        high_util_count = np.sum(comb_arr >= (0.80 * p95_comb))
        high_util_pct = round(float(high_util_count) / len(comb_arr) * 100.0, 2)

        # Downsample points for network payload if necessary
        sampled_points = points
        if len(points) > max_points:
            step = math.ceil(len(points) / max_points)
            sampled_points = points[::step]

        return TractionCircleSummary(
            session_id=session_id,
            sample_count=len(points),
            peak_lateral_g=round(peak_lat, 3),
            peak_braking_g=round(peak_brake, 3),
            peak_longitudinal_accel_g=round(peak_accel, 3),
            peak_combined_g=round(peak_comb, 3),
            p95_combined_g=round(p95_comb, 3),
            high_utilization_pct=high_util_pct,
            points=sampled_points,
        )

    async def compute_braking_performance(self, session_id: uuid.UUID) -> BrakingPerformanceSummary:
        """Detect and quantify braking zones and corner apex deceleration performance."""
        await self._verify_session_exists(session_id)

        stmt = (
            select(
                TelemetryRecord.recorded_at,
                TelemetryRecord.channel_name,
                TelemetryRecord.value,
            )
            .where(
                TelemetryRecord.session_id == session_id,
                TelemetryRecord.channel_name.in_(
                    [
                        "speed_kmh",
                        "Speed",
                        "longitudinal_accel_g",
                        "LongitudinalAccel",
                        "brake_pressure_bar",
                        "Brake",
                    ]
                ),
            )
            .order_by(TelemetryRecord.recorded_at.asc())
        )
        res = await self.db.execute(stmt)
        rows = res.all()

        time_slices: dict[Any, dict[str, float]] = {}
        for recorded_at, channel_name, val in rows:
            norm_name = channel_name.lower()
            if "speed" in norm_name:
                key = "speed"
            elif "long" in norm_name:
                key = "long"
            elif "brake" in norm_name:
                key = "brake"
            else:
                continue

            if recorded_at not in time_slices:
                time_slices[recorded_at] = {}
            time_slices[recorded_at][key] = float(val)

        events: list[BrakingCornerEvent] = []
        in_braking = False
        start_time = None
        initial_speed = 0.0
        min_speed = float("inf")
        apex_time = None
        max_decel = 0.0
        max_brake_press = 0.0

        for ts, data in time_slices.items():
            speed = data.get("speed", 0.0)
            long_accel = data.get("long", 0.0)
            brake_val = data.get("brake", 0.0)

            # Braking condition: negative longitudinal acceleration < -0.6g or brake signal > 5
            is_braking_sample = (long_accel < -0.6) or (brake_val > 5.0)

            if is_braking_sample and speed > 40.0:
                if not in_braking:
                    in_braking = True
                    start_time = ts
                    initial_speed = speed
                    min_speed = speed
                    apex_time = ts
                    max_decel = abs(long_accel)
                    max_brake_press = brake_val
                else:
                    if speed < min_speed:
                        min_speed = speed
                        apex_time = ts
                    max_decel = max(max_decel, abs(long_accel))
                    max_brake_press = max(max_brake_press, brake_val)
            else:
                if in_braking:
                    in_braking = False
                    if start_time and apex_time and (initial_speed - min_speed) >= 15.0:
                        events.append(
                            BrakingCornerEvent(
                                event_index=len(events) + 1,
                                brake_initiation_time=start_time,
                                initial_speed_kmh=round(initial_speed, 1),
                                apex_time=apex_time,
                                apex_min_speed_kmh=round(min_speed, 1),
                                peak_deceleration_g=round(max_decel, 2),
                                peak_brake_pressure_bar=(
                                    round(max_brake_press, 1) if max_brake_press > 0 else None
                                ),
                                speed_loss_kmh=round(initial_speed - min_speed, 1),
                            )
                        )

        # Finalize any trailing braking zone at the end of the telemetry trace
        if in_braking and start_time and apex_time and (initial_speed - min_speed) >= 15.0:
            events.append(
                BrakingCornerEvent(
                    event_index=len(events) + 1,
                    brake_initiation_time=start_time,
                    initial_speed_kmh=round(initial_speed, 1),
                    apex_time=apex_time,
                    apex_min_speed_kmh=round(min_speed, 1),
                    peak_deceleration_g=round(max_decel, 2),
                    peak_brake_pressure_bar=(
                        round(max_brake_press, 1) if max_brake_press > 0 else None
                    ),
                    speed_loss_kmh=round(initial_speed - min_speed, 1),
                )
            )

        if not events:
            return BrakingPerformanceSummary(
                session_id=session_id,
                event_count=0,
                mean_apex_speed_kmh=0.0,
                max_deceleration_g=0.0,
                events=[],
            )

        mean_apex = round(float(np.mean([e.apex_min_speed_kmh for e in events])), 1)
        peak_decel = round(float(np.max([e.peak_deceleration_g for e in events])), 2)

        return BrakingPerformanceSummary(
            session_id=session_id,
            event_count=len(events),
            mean_apex_speed_kmh=mean_apex,
            max_deceleration_g=peak_decel,
            events=events,
        )

    async def compute_powertrain_shifts(self, session_id: uuid.UUID) -> PowertrainShiftSummary:
        """Analyze powertrain transmission upshift points, RPM drops, and gear ratios."""
        await self._verify_session_exists(session_id)

        stmt = (
            select(
                TelemetryRecord.recorded_at,
                TelemetryRecord.channel_name,
                TelemetryRecord.value,
            )
            .where(
                TelemetryRecord.session_id == session_id,
                TelemetryRecord.channel_name.in_(
                    [
                        "engine_rpm",
                        "RPM",
                        "gear",
                        "nGear",
                        "speed_kmh",
                        "Speed",
                    ]
                ),
            )
            .order_by(TelemetryRecord.recorded_at.asc())
        )
        res = await self.db.execute(stmt)
        rows = res.all()

        time_slices: dict[Any, dict[str, float]] = {}
        for recorded_at, channel_name, val in rows:
            norm_name = channel_name.lower()
            if "rpm" in norm_name:
                key = "rpm"
            elif "gear" in norm_name:
                key = "gear"
            elif "speed" in norm_name:
                key = "speed"
            else:
                continue

            if recorded_at not in time_slices:
                time_slices[recorded_at] = {}
            time_slices[recorded_at][key] = float(val)

        shifts: list[GearShiftEvent] = []
        gear_samples: dict[int, list[float]] = {}
        prev_gear: int | None = None
        prev_rpm: float = 0.0

        for ts, data in time_slices.items():
            gear = int(data.get("gear", 0))
            rpm = data.get("rpm", 0.0)
            speed = data.get("speed", 0.0)

            # Record RPM/speed ratio for engaged gears (> 0) and driving speed (> 20 km/h)
            if gear > 0 and speed > 20.0 and rpm > 1000.0:
                if gear not in gear_samples:
                    gear_samples[gear] = []
                gear_samples[gear].append(rpm / speed)

            # Detect upshift: gear increment with sudden RPM drop
            if (
                prev_gear is not None
                and gear > prev_gear >= 1
                and prev_rpm > 5000.0
                and (prev_rpm - rpm) > 500.0
            ):
                shifts.append(
                    GearShiftEvent(
                        from_gear=prev_gear,
                        to_gear=gear,
                        timestamp=ts,
                        pre_shift_rpm=round(prev_rpm, 0),
                        post_shift_rpm=round(rpm, 0),
                        rpm_drop=round(prev_rpm - rpm, 0),
                        vehicle_speed_kmh=round(speed, 1),
                    )
                )

            if gear > 0:
                prev_gear = gear
                prev_rpm = rpm

        gear_ratios: dict[int, float] = {}
        for g, ratios in gear_samples.items():
            gear_ratios[g] = round(float(np.mean(ratios)), 2)

        if not shifts:
            return PowertrainShiftSummary(
                session_id=session_id,
                total_upshifts=0,
                mean_shift_rpm=0.0,
                mean_rpm_drop=0.0,
                gear_ratios_rpm_per_kmh=gear_ratios,
                shifts=[],
            )

        mean_shift_rpm = round(float(np.mean([s.pre_shift_rpm for s in shifts])), 0)
        mean_rpm_drop = round(float(np.mean([s.rpm_drop for s in shifts])), 0)

        return PowertrainShiftSummary(
            session_id=session_id,
            total_upshifts=len(shifts),
            mean_shift_rpm=mean_shift_rpm,
            mean_rpm_drop=mean_rpm_drop,
            gear_ratios_rpm_per_kmh=gear_ratios,
            shifts=shifts,
        )
