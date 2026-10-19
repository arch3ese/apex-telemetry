import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apex_telemetry.models.telemetry import TestSession
from apex_telemetry.schemas.ingestion import TelemetryBatchCreate, TelemetryReadingCreate
from apex_telemetry.services.analysis_service import VehicleDynamicsAnalysisService
from apex_telemetry.services.ingestion_service import IngestionService


async def _create_test_session_with_dynamics(db: AsyncSession) -> uuid.UUID:
    session = TestSession(
        id=uuid.uuid4(),
        session_slug=f"f1-analysis-{uuid.uuid4().hex[:8]}",
        vehicle_id="F1-W15-01",
        track_name="Silverstone Circuit",
    )
    db.add(session)
    await db.commit()

    ingest_svc = IngestionService(db)
    readings: list[TelemetryReadingCreate] = []

    # 1. Straight acceleration phase (gear 3 -> gear 4)
    for i in range(5):
        t = datetime(2026, 10, 19, 10, 0, i, tzinfo=UTC)
        readings.extend(
            [
                TelemetryReadingCreate(
                    channel_name="speed_kmh",
                    recorded_at=t,
                    value=160.0 + (i * 12.0),
                    unit="km/h",
                ),
                TelemetryReadingCreate(
                    channel_name="engine_rpm",
                    recorded_at=t,
                    value=10500.0 + (i * 350.0),
                    unit="rpm",
                ),
                TelemetryReadingCreate(
                    channel_name="gear",
                    recorded_at=t,
                    value=3.0,
                    unit="gear",
                ),
                TelemetryReadingCreate(
                    channel_name="longitudinal_accel_g",
                    recorded_at=t,
                    value=1.5 - (i * 0.1),
                    unit="g",
                ),
                TelemetryReadingCreate(
                    channel_name="lateral_accel_g",
                    recorded_at=t,
                    value=0.05,
                    unit="g",
                ),
            ]
        )

    # Upshift to gear 4 at second 5
    for i in range(5, 10):
        t = datetime(2026, 10, 19, 10, 0, i, tzinfo=UTC)
        readings.extend(
            [
                TelemetryReadingCreate(
                    channel_name="speed_kmh",
                    recorded_at=t,
                    value=210.0 + ((i - 5) * 10.0),
                    unit="km/h",
                ),
                TelemetryReadingCreate(
                    channel_name="engine_rpm",
                    recorded_at=t,
                    value=9600.0 + ((i - 5) * 300.0),  # Dropped from 11,900 to 9,600!
                    unit="rpm",
                ),
                TelemetryReadingCreate(
                    channel_name="gear",
                    recorded_at=t,
                    value=4.0,
                    unit="gear",
                ),
                TelemetryReadingCreate(
                    channel_name="longitudinal_accel_g",
                    recorded_at=t,
                    value=1.0,
                    unit="g",
                ),
                TelemetryReadingCreate(
                    channel_name="lateral_accel_g",
                    recorded_at=t,
                    value=0.02,
                    unit="g",
                ),
            ]
        )

    # 2. Braking & Cornering phase (Heavy braking into Copse / Stowe, seconds 10-15)
    for i in range(10, 15):
        t = datetime(2026, 10, 19, 10, 0, i, tzinfo=UTC)
        step = i - 10
        readings.extend(
            [
                TelemetryReadingCreate(
                    channel_name="speed_kmh",
                    recorded_at=t,
                    value=250.0 - (step * 28.0),  # Slowing down to 138 km/h apex
                    unit="km/h",
                ),
                TelemetryReadingCreate(
                    channel_name="brake_pressure_bar",
                    recorded_at=t,
                    value=90.0 - (step * 15.0),
                    unit="bar",
                ),
                TelemetryReadingCreate(
                    channel_name="longitudinal_accel_g",
                    recorded_at=t,
                    value=-3.5 + (step * 0.4),  # Peak braking -3.5g
                    unit="g",
                ),
                TelemetryReadingCreate(
                    channel_name="lateral_accel_g",
                    recorded_at=t,
                    value=0.5 + (step * 0.6),  # Trail braking into 2.9g lateral
                    unit="g",
                ),
                TelemetryReadingCreate(
                    channel_name="steering_angle_deg",
                    recorded_at=t,
                    value=18.0 + (step * 8.0),
                    unit="deg",
                ),
                TelemetryReadingCreate(
                    channel_name="throttle_pct",
                    recorded_at=t,
                    value=0.0,
                    unit="%",
                ),
            ]
        )

    t15 = datetime(2026, 10, 19, 10, 0, 15, tzinfo=UTC)
    readings.extend(
        [
            TelemetryReadingCreate(
                channel_name="speed_kmh", recorded_at=t15, value=155.0, unit="km/h"
            ),
            TelemetryReadingCreate(
                channel_name="throttle_pct", recorded_at=t15, value=85.0, unit="%"
            ),
            TelemetryReadingCreate(
                channel_name="brake_pressure_bar", recorded_at=t15, value=0.0, unit="bar"
            ),
            TelemetryReadingCreate(
                channel_name="longitudinal_accel_g", recorded_at=t15, value=1.2, unit="g"
            ),
            TelemetryReadingCreate(
                channel_name="lateral_accel_g", recorded_at=t15, value=1.8, unit="g"
            ),
            TelemetryReadingCreate(
                channel_name="steering_angle_deg", recorded_at=t15, value=20.0, unit="deg"
            ),
        ]
    )

    batch = TelemetryBatchCreate(readings=readings)
    await ingest_svc.ingest_batch(session.id, f"dynamics-batch-{uuid.uuid4().hex}", batch)
    return session.id


async def _create_faster_test_session(db: AsyncSession) -> uuid.UUID:
    session = TestSession(
        id=uuid.uuid4(),
        session_slug=f"f1-faster-{uuid.uuid4().hex[:8]}",
        vehicle_id="F1-W15-02",
        track_name="Silverstone Circuit",
    )
    db.add(session)
    await db.commit()

    ingest_svc = IngestionService(db)
    readings: list[TelemetryReadingCreate] = []
    for i in range(16):
        t = datetime(2026, 10, 19, 10, 0, i, tzinfo=UTC)
        speed = 175.0 + (i * 12.0) if i < 10 else 265.0 - ((i - 10) * 18.0)
        readings.append(
            TelemetryReadingCreate(
                channel_name="speed_kmh",
                recorded_at=t,
                value=round(speed, 1),
                unit="km/h",
            )
        )
    batch = TelemetryBatchCreate(readings=readings)
    await ingest_svc.ingest_batch(session.id, f"faster-batch-{uuid.uuid4().hex}", batch)
    return session.id


@pytest.mark.asyncio
async def test_traction_circle_calculation(db_session: AsyncSession):
    session_id = await _create_test_session_with_dynamics(db_session)
    service = VehicleDynamicsAnalysisService(db_session)

    summary = await service.compute_traction_circle(session_id)

    assert summary.session_id == session_id
    assert summary.sample_count > 0
    assert summary.peak_lateral_g >= 2.5
    assert summary.peak_braking_g >= 3.0
    assert summary.peak_combined_g >= 3.5
    assert summary.p95_combined_g > 0.0
    assert summary.high_utilization_pct > 0.0
    assert len(summary.points) == summary.sample_count


@pytest.mark.asyncio
async def test_braking_corner_performance_analysis(db_session: AsyncSession):
    session_id = await _create_test_session_with_dynamics(db_session)
    service = VehicleDynamicsAnalysisService(db_session)

    summary = await service.compute_braking_performance(session_id)

    assert summary.session_id == session_id
    assert summary.event_count >= 1
    assert summary.max_deceleration_g >= 3.0
    first_event = summary.events[0]
    assert first_event.initial_speed_kmh >= 240.0
    assert first_event.apex_min_speed_kmh < 150.0
    assert first_event.speed_loss_kmh > 80.0


@pytest.mark.asyncio
async def test_powertrain_upshift_detection(db_session: AsyncSession):
    session_id = await _create_test_session_with_dynamics(db_session)
    service = VehicleDynamicsAnalysisService(db_session)

    summary = await service.compute_powertrain_shifts(session_id)

    assert summary.session_id == session_id
    assert summary.total_upshifts == 1
    shift = summary.shifts[0]
    assert shift.from_gear == 3
    assert shift.to_gear == 4
    assert shift.rpm_drop >= 1500.0
    assert 3 in summary.gear_ratios_rpm_per_kmh
    assert 4 in summary.gear_ratios_rpm_per_kmh
    # Lower gear has higher RPM per km/h than higher gear
    assert summary.gear_ratios_rpm_per_kmh[3] > summary.gear_ratios_rpm_per_kmh[4]


@pytest.mark.asyncio
async def test_analysis_api_endpoints(api_client, db_session: AsyncSession):
    session_id = await _create_test_session_with_dynamics(db_session)

    # 1. Traction Circle Endpoint
    tc_resp = await api_client.get(f"/api/v1/sessions/{session_id}/analysis/traction-circle")
    assert tc_resp.status_code == 200
    tc_data = tc_resp.json()
    assert tc_data["peak_lateral_g"] > 0.0

    # 2. Braking Performance Endpoint
    brk_resp = await api_client.get(f"/api/v1/sessions/{session_id}/analysis/braking")
    assert brk_resp.status_code == 200
    brk_data = brk_resp.json()
    assert brk_data["event_count"] >= 1

    # 3. Powertrain Shifts Endpoint
    pwr_resp = await api_client.get(f"/api/v1/sessions/{session_id}/analysis/powertrain-shifts")
    assert pwr_resp.status_code == 200
    pwr_data = pwr_resp.json()
    assert pwr_data["total_upshifts"] == 1

    # 4. Traction Circle Plot Image Endpoint
    plot_resp = await api_client.get(f"/api/v1/sessions/{session_id}/analysis/traction-circle/plot")
    assert plot_resp.status_code == 200
    assert "image/png" in plot_resp.headers["content-type"]
    assert len(plot_resp.content) > 1000
    assert plot_resp.content.startswith(b"\x89PNG")

    # 5. Handling Balance Endpoint
    hb_resp = await api_client.get(f"/api/v1/sessions/{session_id}/analysis/handling-balance")
    assert hb_resp.status_code == 200
    hb_data = hb_resp.json()
    assert hb_data["sample_count"] > 0
    assert "understeer_pct" in hb_data

    # 6. Handling Balance Plot Endpoint
    hb_plot_resp = await api_client.get(
        f"/api/v1/sessions/{session_id}/analysis/handling-balance/plot"
    )
    assert hb_plot_resp.status_code == 200
    assert "image/png" in hb_plot_resp.headers["content-type"]
    assert hb_plot_resp.content.startswith(b"\x89PNG")

    # 7. Lap Comparison Endpoint
    comp_session_id = await _create_faster_test_session(db_session)
    comp_resp = await api_client.get(
        f"/api/v1/sessions/{session_id}/analysis/compare-lap/{comp_session_id}"
    )
    assert comp_resp.status_code == 200
    comp_data = comp_resp.json()
    assert comp_data["total_distance_m"] > 0
    assert len(comp_data["points"]) > 0

    # 8. Lap Comparison Plot Endpoint
    comp_plot_resp = await api_client.get(
        f"/api/v1/sessions/{session_id}/analysis/compare-lap/{comp_session_id}/plot"
    )
    assert comp_plot_resp.status_code == 200
    assert "image/png" in comp_plot_resp.headers["content-type"]
    assert comp_plot_resp.content.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_traction_circle_plot_service(db_session: AsyncSession):
    session_id = await _create_test_session_with_dynamics(db_session)
    service = VehicleDynamicsAnalysisService(db_session)

    image_bytes = await service.generate_traction_circle_plot(session_id)
    assert isinstance(image_bytes, bytes)
    assert len(image_bytes) > 1000
    assert image_bytes.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_handling_balance_service(db_session: AsyncSession):
    session_id = await _create_test_session_with_dynamics(db_session)
    service = VehicleDynamicsAnalysisService(db_session)

    summary = await service.compute_handling_balance(session_id)
    assert summary.session_id == session_id
    assert summary.sample_count > 0
    assert (summary.understeer_pct + summary.neutral_pct + summary.oversteer_pct) == pytest.approx(
        100.0, abs=0.2
    )
    assert len(summary.points) == summary.sample_count

    plot_bytes = await service.generate_handling_balance_plot(session_id)
    assert isinstance(plot_bytes, bytes)
    assert len(plot_bytes) > 1000
    assert plot_bytes.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_lap_comparison_service(db_session: AsyncSession):
    ref_session_id = await _create_test_session_with_dynamics(db_session)
    comp_session_id = await _create_faster_test_session(db_session)
    service = VehicleDynamicsAnalysisService(db_session)

    summary = await service.compare_laps(ref_session_id, comp_session_id)
    assert summary.reference_session_id == ref_session_id
    assert summary.comparison_session_id == comp_session_id
    assert summary.total_distance_m > 100.0
    assert summary.final_time_delta_s < 0.0  # Faster session has negative delta!
    assert summary.faster_session_id == comp_session_id
    assert len(summary.points) > 0

    plot_bytes = await service.generate_lap_comparison_plot(ref_session_id, comp_session_id)
    assert isinstance(plot_bytes, bytes)
    assert len(plot_bytes) > 1000
    assert plot_bytes.startswith(b"\x89PNG")
