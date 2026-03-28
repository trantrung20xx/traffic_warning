from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from app.core.config import load_app_config, load_cameras, load_lane_config_for_camera, validate_no_shared_lanes_across_cameras
from app.db.database import create_engine_and_session
from app.managers.camera_context import CameraContext
from app.schemas.camera import CameraConfig
from app.schemas.events import TrackMessage, ViolationEvent


class CameraManager:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cfg = load_app_config(repo_root)

        validate_no_shared_lanes_across_cameras(repo_root)
        self.cameras: list[CameraConfig] = load_cameras(repo_root)

        _, self._SessionLocal = create_engine_and_session(self.cfg.db_path)

        self._contexts: dict[str, CameraContext] = {}
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

        # WebSocket listeners
        self._track_listeners: set[asyncio.Queue[TrackMessage]] = set()
        self._violation_listeners: set[asyncio.Queue[ViolationEvent]] = set()

        # Build contexts lazily in start()

    @property
    def session_factory(self):
        return self._SessionLocal

    def list_cameras(self) -> list[dict]:
        rows = []
        for cam in self.cameras:
            rows.append(
                {
                    "camera_id": cam.camera_id,
                    "camera_type": cam.camera_type,
                    "location": {
                        "road_name": cam.location.road_name,
                        "intersection": cam.location.intersection_name,
                        "gps_lat": cam.location.gps_lat,
                        "gps_lng": cam.location.gps_lng,
                    },
                    "monitored_lanes": cam.monitored_lanes,
                }
            )
        return rows

    def _on_track(self, msg: TrackMessage) -> None:
        # Called inside the event loop (CameraContext.run_forever).
        dead: list[asyncio.Queue] = []
        for q in self._track_listeners:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._track_listeners.discard(q)

    def _on_violation(self, ev: ViolationEvent) -> None:
        dead: list[asyncio.Queue] = []
        for q in self._violation_listeners:
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._violation_listeners.discard(q)

    def create_track_listener(self, *, maxsize: int = 200) -> asyncio.Queue[TrackMessage]:
        q: asyncio.Queue[TrackMessage] = asyncio.Queue(maxsize=maxsize)
        self._track_listeners.add(q)
        return q

    def create_violation_listener(self, *, maxsize: int = 200) -> asyncio.Queue[ViolationEvent]:
        q: asyncio.Queue[ViolationEvent] = asyncio.Queue(maxsize=maxsize)
        self._violation_listeners.add(q)
        return q

    def remove_track_listener(self, q: asyncio.Queue[TrackMessage]) -> None:
        self._track_listeners.discard(q)

    def remove_violation_listener(self, q: asyncio.Queue[ViolationEvent]) -> None:
        self._violation_listeners.discard(q)

    def get_lane_polygons(self, camera_id: str) -> dict:
        ctx = self._contexts.get(camera_id)
        if ctx is None:
            raise KeyError(camera_id)
        return ctx.get_lane_polygons_for_ui()

    def get_camera_preview_jpeg(self, camera_id: str) -> Optional[bytes]:
        ctx = self._contexts.get(camera_id)
        if ctx is None:
            return None
        return ctx.get_latest_preview_jpeg()

    async def start(self) -> None:
        # Create contexts
        if self._contexts:
            return
        for cam in self.cameras:
            lane_cfg = load_lane_config_for_camera(self.repo_root, cam.camera_id)

            ctx = CameraContext(
                camera_config=cam,
                lane_config=lane_cfg,
                db_session_factory=self._SessionLocal,
                on_track=self._on_track,
                on_violation=self._on_violation,
                detector_weights_path="yolov8n.pt",
                detector_conf_threshold=self.cfg.detector_conf_threshold,
                detector_iou_threshold=self.cfg.detector_iou_threshold,
                track_push_interval_ms=self.cfg.track_push_interval_ms,
                wrong_lane_min_duration_ms=self.cfg.wrong_lane_min_duration_ms,
                turn_region_min_hits=self.cfg.turn_region_min_hits,
            )
            self._contexts[cam.camera_id] = ctx

        # Start per-camera tasks
        self._stop_event.clear()
        for ctx in self._contexts.values():
            task = asyncio.create_task(ctx.run_forever(stop_event=self._stop_event))
            self._tasks.append(task)

    async def stop(self) -> None:
        self._stop_event.set()
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()

