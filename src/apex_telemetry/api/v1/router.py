from fastapi import APIRouter

from apex_telemetry.api.v1 import ingestion, records, sessions

api_v1_router = APIRouter()

api_v1_router.include_router(sessions.router)
api_v1_router.include_router(ingestion.router)
api_v1_router.include_router(records.router)
