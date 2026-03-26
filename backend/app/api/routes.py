from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.repository import query_violation_counts
from app.managers.camera_manager import CameraManager


def create_api_router(manager: CameraManager) -> APIRouter:
    router = APIRouter()

    @router.get("/api/health")
    def health():
        return {"status": "ok"}

    @router.get("/api/cameras")
    def list_cameras():
        return {"cameras": manager.list_cameras()}

    @router.get("/api/cameras/{camera_id}/lanes")
    def get_camera_lanes(camera_id: str):
        return manager.get_lane_polygons(camera_id)

    @router.get("/api/stats")
    def stats(
        from_ts: Optional[str] = Query(default=None, description="ISO-8601 timestamp string"),
        to_ts: Optional[str] = Query(default=None, description="ISO-8601 timestamp string"),
    ):
        SessionLocal = manager.session_factory
        with SessionLocal() as session:
            rows = query_violation_counts(session, from_ts=from_ts, to_ts=to_ts)
        return {"rows": rows, "from_timestamp": from_ts, "to_timestamp": to_ts}

    return router

