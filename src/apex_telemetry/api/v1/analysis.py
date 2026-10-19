import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from apex_telemetry.api.deps import get_db
from apex_telemetry.schemas.analysis import (
    BrakingPerformanceSummary,
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
