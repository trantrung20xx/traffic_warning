from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import cv2

from app.core.config import CameraLaneConfig, CameraConfig
from app.db.repository import insert_violation
from app.logic.lane_logic import LaneLogic
from app.logic.violation_logic import ViolationLogic
from app.schemas.camera import CameraLocation
from app.schemas.events import (
    BBox,
    TrackMessage,
    TrackVehicle,
    ViolationEvent,
    ViolationLocation,
)
from app.stats.statistics_engine import StatisticsEngine
from app.rtsp.rtsp_stream import RtspFrameReader
from app.tracking.tracker import YoloByteTrackVehicleTracker
from app.vision.detector import YoloV8VehicleDetector


class CameraContext:
    """
    Per-camera processing pipeline:
    RTSP -> YOLOv8 (vehicles) -> ByteTrack (vehicle_id) -> lane polygon logic -> violation rules -> DB/WS
    """

    def __init__(
        self,
        *,
        camera_config: CameraConfig,
        lane_config: CameraLaneConfig,
        db_session_factory,
        on_track: Callable[[TrackMessage], None],
        on_violation: Callable[[ViolationEvent], None],
        on_log: Optional[Callable[[str], None]] = None,
        detector_weights_path: str = "yolov8n.pt",
        detector_conf_threshold: float = 0.35,
        detector_iou_threshold: float = 0.7,
        track_push_interval_ms: int = 200,
        wrong_lane_min_duration_ms: int = 1200,
        turn_region_min_hits: int = 3,
    ):
        self.camera_config = camera_config
        self.lane_config = lane_config

        self.on_track = on_track
        self.on_violation = on_violation
        self.on_log = on_log or (lambda msg: None)

        # RTSP reader: optionally resize to match polygon coordinates.
        self.rtsp_reader = RtspFrameReader(
            camera_config.rtsp_url,
            frame_width=camera_config.frame_width,
            frame_height=camera_config.frame_height,
        )

        # AI components (backend-only)
        self.detector = YoloV8VehicleDetector(
            weights_path=detector_weights_path,
            conf_threshold=detector_conf_threshold,
            iou_threshold=detector_iou_threshold,
        )
        self.tracker = YoloByteTrackVehicleTracker(self.detector)

        # Lane / violation rules (hand-crafted polygons)
        self.lane_logic = LaneLogic(lane_config.lanes)
        self.violation_logic = ViolationLogic(
            lane_config.lanes,
            wrong_lane_min_duration_ms=wrong_lane_min_duration_ms,
            turn_region_min_hits=turn_region_min_hits,
        )

        self.stats = StatisticsEngine()

        self._db_session_factory = db_session_factory

        # Throttle push to websocket
        self._last_track_push_ts_ms: int = 0
        self._track_push_interval_ms: int = int(track_push_interval_ms)

        # Latest frame for browser preview (MJPEG). Updated from processing loop, read from HTTP handlers.
        self._preview_lock = threading.Lock()
        self._latest_preview_jpeg: Optional[bytes] = None
        self._last_preview_encode_ms: int = 0
        self._preview_min_interval_ms: int = 66  # ~15 FPS max for preview encode

    @property
    def camera_id(self) -> str:
        return self.camera_config.camera_id

    def get_lane_polygons_for_ui(self) -> dict[str, Any]:
        """
        Used by frontend to draw lane polygons on Canvas.
        """
        lanes = []
        for lp in self.lane_config.lanes:
            lanes.append(
                {
                    "lane_id": lp.lane_id,
                    "polygon": lp.polygon,
                    "turn_regions": lp.turn_regions or {},
                    "allowed_maneuvers": lp.allowed_maneuvers or [],
                    "allowed_lane_changes": lp.allowed_lane_changes or [lp.lane_id],
                }
            )
        return {
            "camera_id": self.camera_id,
            "frame_width": self.lane_config.frame_width,
            "frame_height": self.lane_config.frame_height,
            "lanes": lanes,
        }

    def get_latest_preview_jpeg(self) -> Optional[bytes]:
        with self._preview_lock:
            return self._latest_preview_jpeg

    def _maybe_update_preview(self, frame_bgr) -> None:
        """Encode a JPEG occasionally so the web UI can show live video (not just overlays)."""
        now = int(time.time() * 1000)
        if now - self._last_preview_encode_ms < self._preview_min_interval_ms:
            return
        self._last_preview_encode_ms = now
        ok, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        if not ok:
            return
        data = buf.tobytes()
        with self._preview_lock:
            self._latest_preview_jpeg = data

    async def run_forever(self, *, stop_event: asyncio.Event) -> None:
        try:
            while not stop_event.is_set():
                frame = await asyncio.to_thread(self.rtsp_reader.read)
                if frame is None:
                    continue

                await asyncio.to_thread(self._maybe_update_preview, frame.bgr)

                ts_dt = datetime.fromtimestamp(frame.timestamp_utc_ms / 1000.0, tz=timezone.utc)
                tracks = await asyncio.to_thread(self.tracker.track, frame.bgr)

                vehicles: list[TrackVehicle] = []
                violation_candidates: list[tuple[int, str, dict]] = []

                for tr in tracks:
                    lane_id = self.lane_logic.assign_lane_id_from_bbox_xyxy(tr.bbox_xyxy)
                    vehicles.append(
                        TrackVehicle(
                            vehicle_id=tr.vehicle_id,
                            vehicle_type=tr.vehicle_type,
                            lane_id=lane_id,
                            bbox=BBox(x1=tr.bbox_xyxy[0], y1=tr.bbox_xyxy[1], x2=tr.bbox_xyxy[2], y2=tr.bbox_xyxy[3]),
                        )
                    )

                    candidates = self.violation_logic.update_and_maybe_generate_violation(
                        vehicle_id=tr.vehicle_id,
                        vehicle_type=tr.vehicle_type,
                        lane_id=lane_id,
                        bbox_xyxy=tr.bbox_xyxy,
                        ts=ts_dt,
                    )
                    for c in candidates:
                        violation_candidates.append((tr.vehicle_id, tr.vehicle_type, c))

                if frame.timestamp_utc_ms - self._last_track_push_ts_ms >= self._track_push_interval_ms:
                    self._last_track_push_ts_ms = frame.timestamp_utc_ms
                    track_msg = TrackMessage(camera_id=self.camera_id, timestamp=ts_dt, vehicles=vehicles)
                    self.on_track(track_msg)

                if violation_candidates:
                    await self._handle_violations(violation_candidates, ts_dt)

                await asyncio.to_thread(self.violation_logic.prune, current_ts=ts_dt, max_age_s=60.0)
        finally:
            await asyncio.to_thread(self.rtsp_reader.close)

    async def _handle_violations(
        self,
        violation_candidates: list[tuple[int, str, dict]],
        ts_dt: datetime,
    ) -> None:
        camera_loc: CameraLocation = self.camera_config.location
        violation_loc = ViolationLocation(
            road_name=camera_loc.road_name,
            intersection=camera_loc.intersection_name,
            gps_lat=camera_loc.gps_lat,
            gps_lng=camera_loc.gps_lng,
        )

        # Each candidate is (vehicle_id, vehicle_type, {"lane_id":..., "violation":...})
        for vehicle_id, vehicle_type, cand in violation_candidates:

            event = ViolationEvent.from_parts(
                camera_id=self.camera_id,
                location=violation_loc,
                vehicle_id=vehicle_id,
                vehicle_type=vehicle_type,
                lane_id=int(cand["lane_id"]),
                violation=str(cand["violation"]),
                ts=ts_dt,
            )

            # Save to DB (sync call in thread)
            await asyncio.to_thread(self._save_event_to_db, event)

            # Update realtime stats and push over websocket
            self.stats.update_realtime(event)
            self.on_violation(event)

    def _save_event_to_db(self, event: ViolationEvent) -> None:
        with self._db_session_factory() as session:
            insert_violation(session, event)

