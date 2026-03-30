from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.core.config import CameraLaneConfig
from app.managers.camera_manager import CameraManager
from app.schemas.camera import CameraConfig
from app.db.repository import query_violation_counts


def create_api_router(manager: CameraManager) -> APIRouter:
    router = APIRouter()

    @router.get("/api/health")
    def health():
        return {"status": "ok"}

    @router.get("/api/cameras")
    def list_cameras():
        return {"cameras": manager.list_cameras()}

    @router.get("/api/cameras/{camera_id}")
    def get_camera(camera_id: str):
        try:
            return manager.get_camera_detail(camera_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Camera not found")

    @router.post("/api/cameras")
    async def create_camera(payload: dict):
        try:
            camera = CameraConfig.model_validate(payload.get("camera"))
            lane_config = CameraLaneConfig.model_validate(payload.get("lane_config"))
            return manager.upsert_camera(camera, lane_config)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.put("/api/cameras/{camera_id}")
    async def update_camera(camera_id: str, payload: dict):
        try:
            camera = CameraConfig.model_validate({**payload.get("camera", {}), "camera_id": camera_id})
            lane_payload = {**payload.get("lane_config", {}), "camera_id": camera_id}
            lane_config = CameraLaneConfig.model_validate(lane_payload)
            return manager.upsert_camera(camera, lane_config)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.delete("/api/cameras/{camera_id}")
    async def delete_camera(camera_id: str):
        try:
            manager.delete_camera(camera_id)
            return {"ok": True}
        except KeyError:
            raise HTTPException(status_code=404, detail="Camera not found")

    @router.get("/api/cameras/{camera_id}/lanes")
    def get_camera_lanes(camera_id: str):
        try:
            return manager.get_lane_polygons(camera_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Camera not found")

    @router.get("/api/cameras/{camera_id}/preview")
    async def camera_preview_mjpeg(camera_id: str):
        """
        MJPEG stream (multipart/x-mixed-replace) for browser <img src="..."> preview.
        AI inference still runs only in the processing loop; this only serves encoded JPEG snapshots.
        """

        async def frames():
            boundary = b"--frame"
            while True:
                jpg = manager.get_camera_preview_jpeg(camera_id)
                if jpg:
                    yield boundary + b"\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
                await asyncio.sleep(1 / 15)

        return StreamingResponse(
            frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @router.get("/api/violations/history")
    def violation_history(
        camera_id: Optional[str] = Query(default=None),
        from_ts: Optional[str] = Query(default=None),
        to_ts: Optional[str] = Query(default=None),
        limit: int = Query(default=200, ge=1, le=1000),
    ):
        rows = manager.query_history(
            from_ts=from_ts,
            to_ts=to_ts,
            camera_id=camera_id,
            limit=limit,
        )
        return {"rows": rows}

    @router.get("/api/analytics/dashboard")
    def analytics_dashboard(
        camera_id: Optional[str] = Query(default=None),
        from_ts: Optional[str] = Query(default=None),
        to_ts: Optional[str] = Query(default=None),
    ):
        data = manager.query_dashboard(from_ts=from_ts, to_ts=to_ts, camera_id=camera_id)
        return {
            **data,
            "from_timestamp": from_ts,
            "to_timestamp": to_ts,
            "camera_id": camera_id,
        }

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

