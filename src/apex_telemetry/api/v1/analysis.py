import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from apex_telemetry.api.deps import get_db
from apex_telemetry.schemas.analysis import (
    BrakingPerformanceSummary,
    HandlingBalanceSummary,
    LapComparisonSummary,
    PowertrainShiftSummary,
    TractionCircleSummary,
)
from apex_telemetry.services.analysis_service import VehicleDynamicsAnalysisService

router = APIRouter(prefix="/sessions/{session_id}/analysis", tags=["Vehicle Dynamics Analysis"])


@router.get(
    "/traction-circle",
    response_model=TractionCircleSummary,
    summary="Compute G-G friction circle and grip envelope",
)
async def get_traction_circle(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    max_points: Annotated[
        int, Query(ge=10, le=5000, description="Max points to return for plotting")
    ] = 1000,
) -> TractionCircleSummary:
    """Compute lateral vs longitudinal acceleration envelope, combined grip, and friction utilization."""
    service = VehicleDynamicsAnalysisService(db)
    return await service.compute_traction_circle(session_id, max_points=max_points)


@router.get(
    "/traction-circle/plot",
    summary="Render G-G traction circle diagram image",
    responses={
        200: {
            "content": {"image/png": {}},
            "description": "Rendered PNG image of the G-G traction circle friction envelope.",
        }
    },
)
async def get_traction_circle_plot(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    max_points: Annotated[int, Query(ge=50, le=5000, description="Max points to plot")] = 3000,
) -> Response:
    """Render and stream a high-resolution PNG plot of the G-G traction circle."""
    service = VehicleDynamicsAnalysisService(db)
    image_bytes = await service.generate_traction_circle_plot(session_id, max_points=max_points)
    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="traction_circle_{session_id}.png"'},
    )


@router.get(
    "/braking",
    response_model=BrakingPerformanceSummary,
    summary="Analyze braking zones and corner apex speeds",
)
async def get_braking_performance(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BrakingPerformanceSummary:
    """Detect sustained braking zones, entry speeds, corner apex speeds, and peak deceleration."""
    service = VehicleDynamicsAnalysisService(db)
    return await service.compute_braking_performance(session_id)


@router.get(
    "/powertrain-shifts",
    response_model=PowertrainShiftSummary,
    summary="Analyze transmission shift points and gear ratios",
)
async def get_powertrain_shifts(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PowertrainShiftSummary:
    """Quantify transmission upshift points, engine RPM drop transitions, and effective gear ratios."""
    service = VehicleDynamicsAnalysisService(db)
    return await service.compute_powertrain_shifts(session_id)


@router.get(
    "/handling-balance",
    response_model=HandlingBalanceSummary,
    summary="Calculate dynamic understeer angle and cornering balance",
)
async def get_handling_balance(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    wheelbase_m: Annotated[
        float, Query(ge=1.5, le=5.0, description="Vehicle wheelbase in meters")
    ] = 3.6,
    steering_ratio: Annotated[
        float, Query(ge=8.0, le=25.0, description="Steering rack ratio")
    ] = 14.0,
    max_points: Annotated[int, Query(ge=10, le=5000)] = 1500,
) -> HandlingBalanceSummary:
    """Evaluate kinematic Ackerman steering, dynamic understeer angle, and handling state (Segers Ch. 7)."""
    service = VehicleDynamicsAnalysisService(db)
    return await service.compute_handling_balance(
        session_id, wheelbase_m=wheelbase_m, steering_ratio=steering_ratio, max_points=max_points
    )


@router.get(
    "/handling-balance/plot",
    summary="Render 2-pane Handling Balance & Understeer Angle chart",
    responses={
        200: {
            "content": {"image/png": {}},
            "description": "Rendered PNG chart of Steer vs a_y and Understeer Angle vs Distance.",
        }
    },
)
async def get_handling_balance_plot(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    wheelbase_m: Annotated[
        float, Query(ge=1.5, le=5.0, description="Vehicle wheelbase in meters")
    ] = 3.6,
    steering_ratio: Annotated[
        float, Query(ge=8.0, le=25.0, description="Steering rack ratio")
    ] = 14.0,
) -> Response:
    """Render high-resolution PNG chart of Steer Angle vs Lateral Accel and Understeer Angle trace."""
    service = VehicleDynamicsAnalysisService(db)
    image_bytes = await service.generate_handling_balance_plot(
        session_id, wheelbase_m=wheelbase_m, steering_ratio=steering_ratio
    )
    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="handling_balance_{session_id}.png"'},
    )


@router.get(
    "/compare-lap/{comp_session_id}",
    response_model=LapComparisonSummary,
    summary="Spatial distance-domain lap comparison and cumulative delta time",
)
async def compare_laps(
    session_id: uuid.UUID,
    comp_session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    step_distance_m: Annotated[
        float, Query(ge=0.5, le=10.0, description="Resampling distance step in meters")
    ] = 2.0,
    max_points: Annotated[int, Query(ge=10, le=5000)] = 2000,
) -> LapComparisonSummary:
    """Resample telemetry from time to distance domain and calculate cumulative time delta (Segers Ch. 14)."""
    service = VehicleDynamicsAnalysisService(db)
    return await service.compare_laps(
        session_id, comp_session_id, step_distance_m=step_distance_m, max_points=max_points
    )


@router.get(
    "/compare-lap/{comp_session_id}/plot",
    summary="Render pit-wall 2-pane Speed & Cumulative Time Delta (Δt) plot",
    responses={
        200: {
            "content": {"image/png": {}},
            "description": "Rendered PNG chart of Speed vs Distance and Cumulative Time Delta vs Distance.",
        }
    },
)
async def get_lap_comparison_plot(
    session_id: uuid.UUID,
    comp_session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Render pit-wall 2-pane chart with speed overlay and integrated time gain/loss shading."""
    service = VehicleDynamicsAnalysisService(db)
    image_bytes = await service.generate_lap_comparison_plot(session_id, comp_session_id)
    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": f'inline; filename="lap_comparison_{session_id}_vs_{comp_session_id}.png"'
        },
    )
