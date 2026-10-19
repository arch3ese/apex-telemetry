import io
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
    HandlingBalancePoint,
    HandlingBalanceSummary,
    LapComparisonPoint,
    LapComparisonSummary,
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

    async def generate_traction_circle_plot(
        self, session_id: uuid.UUID, max_points: int = 3000
    ) -> bytes:
        """Render a high-resolution G-G traction circle diagram using matplotlib."""
        import matplotlib

        matplotlib.use("Agg")  # Non-interactive backend safe for web servers
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle

        summary = await self.compute_traction_circle(session_id, max_points=max_points)
        if not summary.points:
            fig, ax = plt.subplots(figsize=(8, 8), facecolor="#121212")
            ax.set_facecolor("#121212")
            ax.text(
                0.5,
                0.5,
                "No acceleration telemetry available for this session",
                color="#888888",
                ha="center",
                va="center",
                fontsize=13,
            )
            ax.axis("off")
            buf = io.BytesIO()
            plt.savefig(buf, format="png", bbox_inches="tight", dpi=150)
            plt.close(fig)
            return buf.getvalue()

        lats = [p.lateral_accel_g for p in summary.points]
        longs = [p.longitudinal_accel_g for p in summary.points]
        speeds = [p.speed_kmh if p.speed_kmh is not None else 0.0 for p in summary.points]

        max_g = max(
            summary.peak_lateral_g,
            summary.peak_braking_g,
            summary.peak_longitudinal_accel_g,
            2.0,
        )
        axis_limit = math.ceil(max_g * 1.15 * 2) / 2.0

        fig, ax = plt.subplots(figsize=(9, 9), facecolor="#121212", dpi=150)
        ax.set_facecolor("#181818")

        # Concentric reference circles (1g increments)
        for g_ring in range(1, int(axis_limit) + 1):
            circle = Circle(
                (0, 0),
                g_ring,
                fill=False,
                color="#333333",
                linestyle="--",
                linewidth=1.0,
                alpha=0.7,
                zorder=1,
            )
            ax.add_patch(circle)
            ax.text(
                0.05,
                g_ring + 0.03,
                f"{g_ring}g",
                color="#666666",
                fontsize=8,
                fontweight="bold",
                zorder=2,
            )

        # Crosshairs at origin
        ax.axhline(0, color="#444444", linewidth=1.2, linestyle="-", zorder=1)
        ax.axvline(0, color="#444444", linewidth=1.2, linestyle="-", zorder=1)

        # Theoretical / Measured Friction Envelope
        theta = np.linspace(0, 2 * np.pi, 200)
        env_x = summary.peak_lateral_g * np.cos(theta)
        env_y = np.where(
            np.sin(theta) >= 0,
            summary.peak_longitudinal_accel_g * np.sin(theta),
            -summary.peak_braking_g * np.abs(np.sin(theta)),
        )
        ax.plot(
            env_x,
            env_y,
            color="#00E5FF",
            linestyle="-",
            linewidth=2.0,
            alpha=0.85,
            label="Peak Friction Envelope",
            zorder=3,
        )

        # 95th percentile sustained grip circle
        p95_circle = Circle(
            (0, 0),
            summary.p95_combined_g,
            fill=False,
            color="#FFD700",
            linestyle=":",
            linewidth=1.5,
            alpha=0.85,
            label=f"p95 Sustained Grip ({summary.p95_combined_g:.2f}g)",
            zorder=3,
        )
        ax.add_patch(p95_circle)

        # Scatter points colored by vehicle speed
        has_speed = any(s > 0 for s in speeds)
        if has_speed:
            scatter = ax.scatter(
                lats,
                longs,
                c=speeds,
                cmap="plasma",
                s=20,
                alpha=0.65,
                edgecolors="none",
                zorder=4,
            )
            cbar = plt.colorbar(scatter, ax=ax, pad=0.03, shrink=0.85)
            cbar.set_label("Speed (km/h)", color="#CCCCCC", fontsize=10, labelpad=10)
            cbar.ax.yaxis.set_tick_params(color="#CCCCCC")
            plt.setp(plt.getp(cbar.ax.axes, "yticklabels"), color="#CCCCCC")
            cbar.outline.set_edgecolor("#333333")
        else:
            ax.scatter(
                lats,
                longs,
                color="#00E5FF",
                s=20,
                alpha=0.6,
                edgecolors="none",
                zorder=4,
            )

        ax.set_xlim(-axis_limit, axis_limit)
        ax.set_ylim(-axis_limit, axis_limit)
        ax.set_aspect("equal", adjustable="box")

        ax.set_xlabel(
            "Lateral Acceleration (g)\n[ <--- Turn Left | Turn Right ---> ]",
            color="#E0E0E0",
            fontsize=10,
            labelpad=8,
        )
        ax.set_ylabel(
            "Longitudinal Acceleration (g)\n[ <--- Braking | Throttle ---> ]",
            color="#E0E0E0",
            fontsize=10,
            labelpad=8,
        )
        ax.set_title(
            f"G-G Traction Circle (Friction Envelope)\nSession: {session_id}",
            color="#FFFFFF",
            fontsize=12,
            fontweight="bold",
            pad=15,
        )

        ax.tick_params(colors="#CCCCCC", labelsize=9)
        for spine in ax.spines.values():
            spine.set_color("#333333")

        stats_text = (
            f"Data Points: {summary.sample_count}\n"
            f"Peak Lateral: {summary.peak_lateral_g:.2f}g\n"
            f"Peak Braking: -{summary.peak_braking_g:.2f}g\n"
            f"Peak Throttle: +{summary.peak_longitudinal_accel_g:.2f}g\n"
            f"Peak Combined: {summary.peak_combined_g:.2f}g\n"
            f"p95 Grip: {summary.p95_combined_g:.2f}g\n"
            f"High Utilization: {summary.high_utilization_pct:.1f}%"
        )
        ax.text(
            0.03,
            0.97,
            stats_text,
            transform=ax.transAxes,
            color="#E0E0E0",
            fontsize=8.5,
            verticalalignment="top",
            fontfamily="monospace",
            bbox={
                "boxstyle": "round,pad=0.5",
                "facecolor": "#222222",
                "edgecolor": "#444444",
                "alpha": 0.85,
            },
            zorder=5,
        )

        ax.legend(
            loc="lower right",
            facecolor="#222222",
            edgecolor="#444444",
            fontsize=8,
            labelcolor="#CCCCCC",
        )

        buf = io.BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor(), dpi=150)
        plt.close(fig)
        return buf.getvalue()

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

    async def compute_handling_balance(
        self,
        session_id: uuid.UUID,
        wheelbase_m: float = 3.6,
        steering_ratio: float = 14.0,
        max_points: int = 1500,
    ) -> HandlingBalanceSummary:
        """Calculate dynamic understeer angle and handling state (Segers Ch. 7 / Milliken Ch. 5)."""
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
                        "lateral_accel_g",
                        "LateralAccel",
                        "steering_angle_deg",
                        "SteeringAngle",
                        "Steer",
                        "distance_m",
                        "Distance",
                    ]
                ),
            )
            .order_by(TelemetryRecord.recorded_at.asc())
        )
        res = await self.db.execute(stmt)
        rows = res.all()

        time_slices: dict[Any, dict[str, float]] = {}
        for recorded_at, channel_name, val in rows:
            norm = channel_name.lower()
            if "speed" in norm:
                key = "speed"
            elif "lat" in norm:
                key = "lat"
            elif "steer" in norm:
                key = "steer"
            elif "dist" in norm:
                key = "dist"
            else:
                continue

            if recorded_at not in time_slices:
                time_slices[recorded_at] = {}
            time_slices[recorded_at][key] = float(val)

        points: list[HandlingBalancePoint] = []
        cumulative_dist = 0.0
        prev_time = None
        prev_speed = 0.0

        for ts, data in time_slices.items():
            speed_kmh = data.get("speed", 0.0)
            lat_g = data.get("lat", 0.0)
            steer_deg = data.get("steer", 0.0)

            # Cumulative distance tracking if not logged
            if "dist" in data:
                dist_m = data["dist"]
            else:
                if prev_time is not None:
                    dt = (ts - prev_time).total_seconds()
                    avg_v_ms = ((speed_kmh + prev_speed) / 2.0) / 3.6
                    cumulative_dist += avg_v_ms * dt
                dist_m = cumulative_dist

            prev_time = ts
            prev_speed = speed_kmh

            v_ms = speed_kmh / 3.6
            abs_lat_g = abs(lat_g)

            # Only analyze active cornering phases (speed > 20 km/h, lateral load >= 0.25g)
            if v_ms >= 5.5 and abs_lat_g >= 0.25:
                # Kinematic Ackerman steering angle at wheel: delta_Ack = L / R = (L * a_y) / V^2
                # In degrees at the steering wheel: delta_Ack_deg = (L * a_y / V^2) * (180 / pi) * steering_ratio
                a_y_ms2 = abs_lat_g * 9.80665
                ackerman_rad = (wheelbase_m * a_y_ms2) / (v_ms * v_ms)
                ackerman_deg = ackerman_rad * (180.0 / math.pi) * steering_ratio

                # Dynamic Understeer Angle: alpha_u = |steer| - ackerman
                understeer_angle = abs(steer_deg) - ackerman_deg

                if understeer_angle > 1.5:
                    state = "understeer"
                elif understeer_angle < -1.5:
                    state = "oversteer"
                else:
                    state = "neutral"

                points.append(
                    HandlingBalancePoint(
                        distance_m=round(dist_m, 1),
                        speed_kmh=round(speed_kmh, 1),
                        lateral_accel_g=round(lat_g, 3),
                        steering_angle_deg=round(steer_deg, 2),
                        ackerman_angle_deg=round(ackerman_deg, 2),
                        understeer_angle_deg=round(understeer_angle, 2),
                        handling_state=state,
                    )
                )

        if not points:
            return HandlingBalanceSummary(
                session_id=session_id,
                wheelbase_m=wheelbase_m,
                steering_ratio=steering_ratio,
                sample_count=0,
                understeer_pct=0.0,
                neutral_pct=0.0,
                oversteer_pct=0.0,
                max_understeer_deg=0.0,
                max_oversteer_deg=0.0,
                mean_understeer_deg=0.0,
                points=[],
            )

        u_count = sum(1 for p in points if p.handling_state == "understeer")
        n_count = sum(1 for p in points if p.handling_state == "neutral")
        o_count = sum(1 for p in points if p.handling_state == "oversteer")
        total = len(points)

        u_angles = [p.understeer_angle_deg for p in points]
        max_u = max(0.0, max(u_angles))
        max_o = abs(min(0.0, min(u_angles)))
        mean_u = round(float(np.mean(u_angles)), 2)

        sampled = points
        if len(points) > max_points:
            step = math.ceil(len(points) / max_points)
            sampled = points[::step]

        return HandlingBalanceSummary(
            session_id=session_id,
            wheelbase_m=wheelbase_m,
            steering_ratio=steering_ratio,
            sample_count=total,
            understeer_pct=round((u_count / total) * 100.0, 1),
            neutral_pct=round((n_count / total) * 100.0, 1),
            oversteer_pct=round((o_count / total) * 100.0, 1),
            max_understeer_deg=round(max_u, 2),
            max_oversteer_deg=round(max_o, 2),
            mean_understeer_deg=mean_u,
            points=sampled,
        )

    async def generate_handling_balance_plot(
        self,
        session_id: uuid.UUID,
        wheelbase_m: float = 3.6,
        steering_ratio: float = 14.0,
    ) -> bytes:
        """Render a 2-pane Handling Balance & Understeer Angle telemetry chart."""
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        summary = await self.compute_handling_balance(
            session_id, wheelbase_m=wheelbase_m, steering_ratio=steering_ratio
        )

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), facecolor="#121212", dpi=150)
        ax1.set_facecolor("#181818")
        ax2.set_facecolor("#181818")

        if not summary.points:
            ax1.text(0.5, 0.5, "Insufficient cornering telemetry", color="#888", ha="center")
            buf = io.BytesIO()
            plt.savefig(buf, format="png", bbox_inches="tight", dpi=150)
            plt.close(fig)
            return buf.getvalue()

        dists = [p.distance_m for p in summary.points]
        u_angles = [p.understeer_angle_deg for p in summary.points]
        lat_gs = [abs(p.lateral_accel_g) for p in summary.points]
        steer_degs = [abs(p.steering_angle_deg) for p in summary.points]
        ack_degs = [p.ackerman_angle_deg for p in summary.points]
        speeds = [p.speed_kmh for p in summary.points]

        # Top Plot: Steer Angle vs Lateral Accel with theoretical Ackerman curve
        sc = ax1.scatter(lat_gs, steer_degs, c=speeds, cmap="plasma", s=18, alpha=0.7, zorder=3)
        # Sort for clean theoretical line
        sort_idx = np.argsort(lat_gs)
        ax1.plot(
            np.array(lat_gs)[sort_idx],
            np.array(ack_degs)[sort_idx],
            color="#00E5FF",
            linestyle="--",
            linewidth=1.8,
            label="Theoretical Neutral Ackerman Angle",
            zorder=2,
        )
        cbar = plt.colorbar(sc, ax=ax1, pad=0.02, shrink=0.85)
        cbar.set_label("Speed (km/h)", color="#CCC", fontsize=9)
        cbar.ax.yaxis.set_tick_params(color="#CCC")
        plt.setp(plt.getp(cbar.ax.axes, "yticklabels"), color="#CCC")

        ax1.set_xlabel("Lateral Acceleration |a_y| (g)", color="#E0E0E0", fontsize=9)
        ax1.set_ylabel("Steering Wheel Angle |δ_sw| (°)", color="#E0E0E0", fontsize=9)
        ax1.set_title(
            f"Handling Balance & Understeer Gradient (Segers Ch. 7)\nSession: {session_id}",
            color="#FFF",
            fontsize=11,
            fontweight="bold",
        )
        ax1.legend(
            loc="upper left", facecolor="#222", edgecolor="#444", fontsize=8, labelcolor="#CCC"
        )
        ax1.tick_params(colors="#CCC")
        ax1.grid(True, color="#333", linestyle=":")

        # Bottom Plot: Dynamic Understeer Angle vs Distance
        ax2.axhspan(-1.5, 1.5, color="#00E676", alpha=0.15, label="Neutral Zone [-1.5°, +1.5°]")
        ax2.axhline(0, color="#666", linestyle="-", linewidth=1.0)
        ax2.plot(dists, u_angles, color="#FF9100", linewidth=1.5, label="Understeer Angle α_u(s)")
        ax2.fill_between(
            dists,
            u_angles,
            1.5,
            where=(np.array(u_angles) > 1.5),
            color="#FF3D00",
            alpha=0.3,
            label="Understeer (Front Scrub)",
        )
        ax2.fill_between(
            dists,
            u_angles,
            -1.5,
            where=(np.array(u_angles) < -1.5),
            color="#7C4DFF",
            alpha=0.3,
            label="Oversteer (Opposite Lock)",
        )

        ax2.set_xlabel("Track Distance (m)", color="#E0E0E0", fontsize=9)
        ax2.set_ylabel("Understeer Angle α_u (°)", color="#E0E0E0", fontsize=9)
        ax2.tick_params(colors="#CCC")
        ax2.grid(True, color="#333", linestyle=":")
        ax2.legend(
            loc="upper right", facecolor="#222", edgecolor="#444", fontsize=7.5, labelcolor="#CCC"
        )

        stat_box = (
            f"Understeer: {summary.understeer_pct}%\n"
            f"Neutral:    {summary.neutral_pct}%\n"
            f"Oversteer:  {summary.oversteer_pct}%\n"
            f"Peak Scrub: +{summary.max_understeer_deg}°\n"
            f"Peak Slide: -{summary.max_oversteer_deg}°"
        )
        ax2.text(
            0.02,
            0.95,
            stat_box,
            transform=ax2.transAxes,
            color="#E0E0E0",
            fontsize=8,
            verticalalignment="top",
            fontfamily="monospace",
            bbox={
                "boxstyle": "round,pad=0.4",
                "facecolor": "#222",
                "edgecolor": "#444",
                "alpha": 0.85,
            },
        )

        for ax in (ax1, ax2):
            for spine in ax.spines.values():
                spine.set_color("#333")

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor(), dpi=150)
        plt.close(fig)
        return buf.getvalue()

    async def _get_distance_series(
        self, session_id: uuid.UUID
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Fetch and calculate distance, speed, throttle, and brake series for a session."""
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
                        "throttle_pct",
                        "Throttle",
                        "brake_pressure_bar",
                        "Brake",
                        "distance_m",
                        "Distance",
                    ]
                ),
            )
            .order_by(TelemetryRecord.recorded_at.asc())
        )
        rows = (await self.db.execute(stmt)).all()

        slices: dict[Any, dict[str, float]] = {}
        for ts, ch, val in rows:
            norm = ch.lower()
            if "speed" in norm:
                k = "speed"
            elif "throttle" in norm:
                k = "throttle"
            elif "brake" in norm:
                k = "brake"
            elif "dist" in norm:
                k = "dist"
            else:
                continue

            if ts not in slices:
                slices[ts] = {}
            slices[ts][k] = float(val)

        dists, speeds, throttles, brakes = [], [], [], []
        cumulative = 0.0
        prev_ts = None
        prev_spd = 0.0

        for ts, data in sorted(slices.items()):
            spd = data.get("speed", 0.0)
            if "dist" in data:
                d = data["dist"]
            else:
                if prev_ts is not None:
                    dt = (ts - prev_ts).total_seconds()
                    cumulative += (((spd + prev_spd) / 2.0) / 3.6) * dt
                d = cumulative

            prev_ts = ts
            prev_spd = spd

            dists.append(d)
            speeds.append(spd)
            throttles.append(data.get("throttle", 0.0))
            brakes.append(data.get("brake", 0.0))

        return np.array(dists), np.array(speeds), np.array(throttles), np.array(brakes)

    async def compare_laps(
        self,
        ref_session_id: uuid.UUID,
        comp_session_id: uuid.UUID,
        step_distance_m: float = 2.0,
        max_points: int = 2000,
    ) -> LapComparisonSummary:
        """Compare two sessions across track distance, calculating cumulative time delta (Segers Ch. 14)."""
        await self._verify_session_exists(ref_session_id)
        await self._verify_session_exists(comp_session_id)

        d_ref, s_ref, t_ref, b_ref = await self._get_distance_series(ref_session_id)
        d_comp, s_comp, t_comp, b_comp = await self._get_distance_series(comp_session_id)

        if len(d_ref) < 2 or len(d_comp) < 2:
            return LapComparisonSummary(
                reference_session_id=ref_session_id,
                comparison_session_id=comp_session_id,
                total_distance_m=0.0,
                final_time_delta_s=0.0,
                faster_session_id=ref_session_id,
                max_time_loss_s=0.0,
                max_time_gain_s=0.0,
                points=[],
            )

        max_dist = min(float(np.max(d_ref)), float(np.max(d_comp)))
        dist_grid = np.arange(0.0, max_dist, step_distance_m)

        # Interpolate speed and inputs onto common spatial grid
        v_ref_grid = np.interp(dist_grid, d_ref, s_ref)
        v_comp_grid = np.interp(dist_grid, d_comp, s_comp)
        thr_ref_grid = np.interp(dist_grid, d_ref, t_ref)
        thr_comp_grid = np.interp(dist_grid, d_comp, t_comp)
        brk_ref_grid = np.interp(dist_grid, d_ref, b_ref)
        brk_comp_grid = np.interp(dist_grid, d_comp, b_comp)

        # Cumulative time delta integration: delta_t(s) = integral(1/V_comp - 1/V_ref) ds
        # V in m/s: V_ms = V_kmh / 3.6
        v_ref_ms = np.maximum(v_ref_grid / 3.6, 1.0)  # avoid division by zero
        v_comp_ms = np.maximum(v_comp_grid / 3.6, 1.0)

        dt_differential = (1.0 / v_comp_ms) - (1.0 / v_ref_ms)
        # Numerical integration using cumulative trapezoid
        cumulative_dt = np.zeros_like(dist_grid)
        if len(dist_grid) > 1:
            ds = np.diff(dist_grid)
            cumulative_dt[1:] = np.cumsum(((dt_differential[:-1] + dt_differential[1:]) / 2.0) * ds)

        points: list[LapComparisonPoint] = []
        for i, d in enumerate(dist_grid):
            points.append(
                LapComparisonPoint(
                    distance_m=round(float(d), 1),
                    ref_speed_kmh=round(float(v_ref_grid[i]), 1),
                    comp_speed_kmh=round(float(v_comp_grid[i]), 1),
                    speed_delta_kmh=round(float(v_comp_grid[i] - v_ref_grid[i]), 1),
                    cumulative_time_delta_s=round(float(cumulative_dt[i]), 3),
                    ref_throttle=round(float(thr_ref_grid[i]), 1),
                    comp_throttle=round(float(thr_comp_grid[i]), 1),
                    ref_brake=round(float(brk_ref_grid[i]), 1),
                    comp_brake=round(float(brk_comp_grid[i]), 1),
                )
            )

        final_dt = float(cumulative_dt[-1]) if len(cumulative_dt) > 0 else 0.0
        faster_id = comp_session_id if final_dt < 0 else ref_session_id

        # Downsample points for network payload if needed
        sampled = points
        if len(points) > max_points:
            step = math.ceil(len(points) / max_points)
            sampled = points[::step]

        return LapComparisonSummary(
            reference_session_id=ref_session_id,
            comparison_session_id=comp_session_id,
            total_distance_m=round(max_dist, 1),
            final_time_delta_s=round(final_dt, 3),
            faster_session_id=faster_id,
            max_time_loss_s=round(float(np.max(cumulative_dt)), 3),
            max_time_gain_s=round(abs(float(np.min(cumulative_dt))), 3),
            points=sampled,
        )

    async def generate_lap_comparison_plot(
        self, ref_session_id: uuid.UUID, comp_session_id: uuid.UUID
    ) -> bytes:
        """Render the pit-wall standard 2-pane Distance-Based Lap Comparison & Time Delta plot."""
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        summary = await self.compare_laps(ref_session_id, comp_session_id, step_distance_m=1.0)

        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(11, 7), facecolor="#121212", dpi=150, sharex=True
        )
        ax1.set_facecolor("#181818")
        ax2.set_facecolor("#181818")

        if not summary.points:
            ax1.text(
                0.5,
                0.5,
                "Insufficient distance telemetry for lap comparison",
                color="#888",
                ha="center",
            )
            buf = io.BytesIO()
            plt.savefig(buf, format="png", bbox_inches="tight", dpi=150)
            plt.close(fig)
            return buf.getvalue()

        dists = [p.distance_m for p in summary.points]
        v_ref = [p.ref_speed_kmh for p in summary.points]
        v_comp = [p.comp_speed_kmh for p in summary.points]
        time_deltas = [p.cumulative_time_delta_s for p in summary.points]

        # Top Plot: Speed vs Track Distance
        ax1.plot(
            dists,
            v_ref,
            color="#00E5FF",
            linewidth=1.5,
            label=f"Ref Lap: {str(ref_session_id)[:8]}...",
        )
        ax1.plot(
            dists,
            v_comp,
            color="#FF007F",
            linewidth=1.5,
            linestyle="--",
            label=f"Comp Lap: {str(comp_session_id)[:8]}...",
        )
        ax1.set_ylabel("Speed (km/h)", color="#E0E0E0", fontsize=9)
        ax1.set_title(
            f"Distance-Domain Telemetry Overlay & Delta Time (Δt vs. Distance)\nRef: {ref_session_id} vs Comp: {comp_session_id}",
            color="#FFFFFF",
            fontsize=11,
            fontweight="bold",
        )
        ax1.legend(
            loc="lower left", facecolor="#222", edgecolor="#444", fontsize=8, labelcolor="#CCC"
        )
        ax1.tick_params(colors="#CCC")
        ax1.grid(True, color="#333", linestyle=":")

        # Bottom Plot: Cumulative Time Delta vs Distance
        ax2.axhline(0, color="#666666", linestyle="-", linewidth=1.0)
        dt_arr = np.array(time_deltas)
        ax2.plot(dists, dt_arr, color="#FFD700", linewidth=1.8, label="Cumulative Time Delta Δt(s)")

        # Shading: Green when comp is gaining time (< 0), Red when losing time (> 0)
        ax2.fill_between(
            dists,
            dt_arr,
            0,
            where=(dt_arr < 0),
            color="#00E676",
            alpha=0.3,
            label="Comp Lap Ahead / Gaining",
        )
        ax2.fill_between(
            dists,
            dt_arr,
            0,
            where=(dt_arr > 0),
            color="#FF1744",
            alpha=0.3,
            label="Ref Lap Ahead / Gaining",
        )

        ax2.set_xlabel("Track Distance (m)", color="#E0E0E0", fontsize=9)
        ax2.set_ylabel("Time Delta Δt (s)", color="#E0E0E0", fontsize=9)
        ax2.tick_params(colors="#CCC")
        ax2.grid(True, color="#333", linestyle=":")
        ax2.legend(
            loc="lower left", facecolor="#222", edgecolor="#444", fontsize=8, labelcolor="#CCC"
        )

        # Metric summary banner
        gap_sign = "+" if summary.final_time_delta_s > 0 else ""
        winner_text = "Comp Lap Faster" if summary.final_time_delta_s < 0 else "Ref Lap Faster"
        ax2.text(
            0.02,
            0.90,
            f"Lap Gap: {gap_sign}{summary.final_time_delta_s:.3f}s ({winner_text})\nTrack Distance: {summary.total_distance_m:.0f} m",
            transform=ax2.transAxes,
            color="#FFFFFF",
            fontsize=9,
            fontweight="bold",
            bbox={
                "boxstyle": "round,pad=0.4",
                "facecolor": "#222222",
                "edgecolor": "#444444",
                "alpha": 0.85,
            },
        )

        for ax in (ax1, ax2):
            for spine in ax.spines.values():
                spine.set_color("#333")

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor(), dpi=150)
        plt.close(fig)
        return buf.getvalue()
