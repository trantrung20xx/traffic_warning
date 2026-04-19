from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import cv2

from app.core.config import CameraConfig, RuntimeCameraLaneConfig
from app.core.evidence_images import build_evidence_image_url, save_evidence_image
from app.db.repository import insert_violation
from app.logic.lane_logic import LaneLogic, TemporalLaneAssigner
from app.logic.track_id_logic import StableTrackIdAssigner
from app.logic.vehicle_type_logic import TemporalVehicleTypeAssigner
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
        repo_root: Path,
        camera_config: CameraConfig,
        lane_config: RuntimeCameraLaneConfig,
        db_session_factory,
        on_track: Callable[[TrackMessage], None],
        on_violation: Callable[[ViolationEvent], None],
        on_log: Optional[Callable[[str], None]] = None,
        detector_weights_path: str = "yolov8n.pt",
        detector_device: str = "auto",
        detector_conf_threshold: float = 0.35,
        detector_iou_threshold: float = 0.7,
        tracker_config: str = "bytetrack.yaml",
        vehicle_type_history_window_ms: int = 4000,
        vehicle_type_history_size: int = 12,
        stable_track_max_idle_ms: int = 1500,
        stable_track_min_iou_for_rebind: float = 0.15,
        stable_track_max_normalized_distance: float = 1.6,
        temporal_lane_observation_window_ms: int = 1200,
        temporal_lane_min_majority_hits: int = 3,
        temporal_lane_switch_min_duration_ms: int = 700,
        track_push_interval_ms: int = 200,
        wrong_lane_min_duration_ms: int = 1200,
        turn_region_min_hits: int = 3,
        state_prune_max_age_s: float = 60.0,
        rtsp_reconnect_delay_s: float = 2.0,
        preview_max_fps: float = 15.0,
        preview_jpeg_quality: int = 75,
        processing_fps_window_s: float = 1.5,
        evidence_crop_expand_x_ratio: float = 0.28,
        evidence_crop_expand_y_top_ratio: float = 0.32,
        evidence_crop_expand_y_bottom_ratio: float = 0.27,
        evidence_crop_min_size_px: int = 24,
        evidence_jpeg_quality: int = 92,
    ):
        self.repo_root = Path(repo_root)
        self.camera_config = camera_config
        self.lane_config = lane_config

        self.on_track = on_track
        self.on_violation = on_violation
        self.on_log = on_log or (lambda msg: None)

        # RTSP reader: resize into the configured frame space that normalized
        # polygons are denormalized into before lane/violation logic runs.
        self.rtsp_reader = RtspFrameReader(
            camera_config.rtsp_url,
            reconnect_delay_s=rtsp_reconnect_delay_s,
            frame_width=camera_config.frame_width,
            frame_height=camera_config.frame_height,
        )

        # AI components (backend-only)
        self.detector = YoloV8VehicleDetector(
            weights_path=detector_weights_path,
            device=detector_device,
            conf_threshold=detector_conf_threshold,
            iou_threshold=detector_iou_threshold,
        )
        self.tracker = YoloByteTrackVehicleTracker(self.detector, tracker_config=tracker_config)
        self.on_log(
            f"[{self.camera_id}] detector={detector_weights_path} requested_device={self.detector.requested_device} "
            f"resolved_device={self.detector.device}"
        )

        # Lane / violation rules (hand-crafted polygons)
        self.lane_logic = LaneLogic(lane_config.lanes)
        self.stable_track_id_assigner = StableTrackIdAssigner(
            max_idle_ms=stable_track_max_idle_ms,
            min_iou_for_rebind=stable_track_min_iou_for_rebind,
            max_normalized_distance=stable_track_max_normalized_distance,
        )
        self.temporal_lane_assigner = TemporalLaneAssigner(
            observation_window_ms=temporal_lane_observation_window_ms,
            min_majority_hits=temporal_lane_min_majority_hits,
            switch_min_duration_ms=temporal_lane_switch_min_duration_ms,
        )
        self.temporal_vehicle_type_assigner = TemporalVehicleTypeAssigner(
            history_window_ms=vehicle_type_history_window_ms,
            history_size=vehicle_type_history_size,
        )
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
        self._state_prune_max_age_s: float = float(state_prune_max_age_s)
        self._processed_frame_times_s: deque[float] = deque()
        self._processing_fps: Optional[float] = None
        self._processing_fps_window_s: float = float(processing_fps_window_s)

        # Latest frame for browser preview (MJPEG). Updated from processing loop, read from HTTP handlers.
        self._preview_lock = threading.Lock()
        self._latest_preview_jpeg: Optional[bytes] = None
        self._last_preview_encode_ms: int = 0
        self._preview_jpeg_quality: int = int(preview_jpeg_quality)
        safe_preview_fps = max(float(preview_max_fps), 0.1)
        self._preview_min_interval_ms: int = max(int(round(1000.0 / safe_preview_fps)), 1)
        self._evidence_crop_expand_x_ratio: float = float(evidence_crop_expand_x_ratio)
        self._evidence_crop_expand_y_top_ratio: float = float(evidence_crop_expand_y_top_ratio)
        self._evidence_crop_expand_y_bottom_ratio: float = float(evidence_crop_expand_y_bottom_ratio)
        self._evidence_crop_min_size_px: int = int(evidence_crop_min_size_px)
        self._evidence_jpeg_quality: int = int(evidence_jpeg_quality)

    @property
    def camera_id(self) -> str:
        return self.camera_config.camera_id

    def get_lane_polygons_for_ui(self) -> dict[str, Any]:
        """
        Used by frontend to draw lane polygons on Canvas in pixel space.
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
                    "allowed_vehicle_types": lp.allowed_vehicle_types or ["motorcycle", "car", "truck", "bus"],
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
        ok, buf = cv2.imencode(
            ".jpg",
            frame_bgr,
            [int(cv2.IMWRITE_JPEG_QUALITY), self._preview_jpeg_quality],
        )
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
                tracks = await asyncio.to_thread(self.stable_track_id_assigner.assign, raw_tracks=tracks, ts=ts_dt)

                vehicles: list[TrackVehicle] = []
                violation_candidates: list[tuple[int, str, dict, list[float]]] = []

                for tr in tracks:
                    vehicle_type = self.temporal_vehicle_type_assigner.resolve_type(
                        vehicle_id=tr.vehicle_id,
                        predicted_type=tr.vehicle_type,
                        confidence=tr.confidence,
                        ts=ts_dt,
                    )
                    raw_lane_id = self.lane_logic.assign_lane_id_from_bbox_xyxy(tr.bbox_xyxy)
                    lane_id = self.temporal_lane_assigner.resolve_lane(
                        vehicle_id=tr.vehicle_id,
                        raw_lane_id=raw_lane_id,
                        ts=ts_dt,
                    )
                    vehicles.append(
                        TrackVehicle(
                            vehicle_id=tr.vehicle_id,
                            vehicle_type=vehicle_type,
                            lane_id=lane_id,
                            raw_lane_id=raw_lane_id,
                            bbox=BBox(x1=tr.bbox_xyxy[0], y1=tr.bbox_xyxy[1], x2=tr.bbox_xyxy[2], y2=tr.bbox_xyxy[3]),
                        )
                    )

                    candidates = self.violation_logic.update_and_maybe_generate_violation(
                        vehicle_id=tr.vehicle_id,
                        vehicle_type=vehicle_type,
                        lane_id=lane_id,
                        bbox_xyxy=tr.bbox_xyxy,
                        ts=ts_dt,
                    )
                    for c in candidates:
                        violation_candidates.append((tr.vehicle_id, vehicle_type, c, list(tr.bbox_xyxy)))

                if frame.timestamp_utc_ms - self._last_track_push_ts_ms >= self._track_push_interval_ms:
                    self._last_track_push_ts_ms = frame.timestamp_utc_ms
                    track_msg = TrackMessage(
                        camera_id=self.camera_id,
                        timestamp=ts_dt,
                        processing_fps=self._processing_fps,
                        vehicles=vehicles,
                    )
                    self.on_track(track_msg)

                if violation_candidates:
                    await self._handle_violations(
                        violation_candidates,
                        ts_dt,
                        frame_bgr=frame.bgr,
                        frame_timestamp_utc_ms=frame.timestamp_utc_ms,
                    )

                await asyncio.to_thread(
                    self.violation_logic.prune,
                    current_ts=ts_dt,
                    max_age_s=self._state_prune_max_age_s,
                )
                await asyncio.to_thread(
                    self.stable_track_id_assigner.prune,
                    current_ts=ts_dt,
                    max_age_s=self._state_prune_max_age_s,
                )
                await asyncio.to_thread(
                    self.temporal_lane_assigner.prune,
                    current_ts=ts_dt,
                    max_age_s=self._state_prune_max_age_s,
                )
                await asyncio.to_thread(
                    self.temporal_vehicle_type_assigner.prune,
                    current_ts=ts_dt,
                    max_age_s=self._state_prune_max_age_s,
                )
                self._mark_processed_frame()
        finally:
            await asyncio.to_thread(self.rtsp_reader.close)

    def _mark_processed_frame(self) -> None:
        now_s = time.perf_counter()
        self._processed_frame_times_s.append(now_s)
        cutoff_s = now_s - self._processing_fps_window_s
        while self._processed_frame_times_s and self._processed_frame_times_s[0] < cutoff_s:
            self._processed_frame_times_s.popleft()

        if len(self._processed_frame_times_s) >= 2:
            duration_s = self._processed_frame_times_s[-1] - self._processed_frame_times_s[0]
            self._processing_fps = (
                (len(self._processed_frame_times_s) - 1) / duration_s if duration_s > 0 else None
            )
        else:
            self._processing_fps = None

    async def _handle_violations(
        self,
        violation_candidates: list[tuple[int, str, dict, list[float]]],
        ts_dt: datetime,
        *,
        frame_bgr,
        frame_timestamp_utc_ms: int,
    ) -> None:
        camera_loc: CameraLocation = self.camera_config.location
        violation_loc = ViolationLocation(
            road_name=camera_loc.road_name,
            intersection=camera_loc.intersection_name,
            gps_lat=camera_loc.gps_lat,
            gps_lng=camera_loc.gps_lng,
        )

        # Each candidate is (vehicle_id, vehicle_type, {"lane_id":..., "violation":...}, bbox_xyxy)
        for vehicle_id, vehicle_type, cand, bbox_xyxy in violation_candidates:
            image_path = await asyncio.to_thread(
                self._create_violation_evidence,
                frame_bgr,
                bbox_xyxy,
                frame_timestamp_utc_ms=frame_timestamp_utc_ms,
                vehicle_id=vehicle_id,
                lane_id=int(cand["lane_id"]),
                violation=str(cand["violation"]),
            )

            event = ViolationEvent.from_parts(
                camera_id=self.camera_id,
                location=violation_loc,
                vehicle_id=vehicle_id,
                vehicle_type=vehicle_type,
                lane_id=int(cand["lane_id"]),
                violation=str(cand["violation"]),
                image_path=image_path,
                image_url=build_evidence_image_url(image_path),
                ts=ts_dt,
            )

            # Save to DB (sync call in thread)
            await asyncio.to_thread(self._save_event_to_db, event)

            # Update realtime stats and push over websocket
            self.stats.update_realtime(event)
            self.on_violation(event)

    def _crop_violation_evidence(self, frame_bgr, bbox_xyxy: list[float]):
        frame_height, frame_width = frame_bgr.shape[:2]
        if frame_height <= 0 or frame_width <= 0:
            return frame_bgr

        x1, y1, x2, y2 = [float(value) for value in bbox_xyxy]
        box_width = max(x2 - x1, 1.0)
        box_height = max(y2 - y1, 1.0)

        expand_x = box_width * self._evidence_crop_expand_x_ratio
        expand_y_top = box_height * self._evidence_crop_expand_y_top_ratio
        expand_y_bottom = box_height * self._evidence_crop_expand_y_bottom_ratio

        crop_x1 = max(int(round(x1 - expand_x)), 0)
        crop_y1 = max(int(round(y1 - expand_y_top)), 0)
        crop_x2 = min(int(round(x2 + expand_x)), frame_width)
        crop_y2 = min(int(round(y2 + expand_y_bottom)), frame_height)

        if (
            crop_x2 - crop_x1 < self._evidence_crop_min_size_px
            or crop_y2 - crop_y1 < self._evidence_crop_min_size_px
        ):
            return frame_bgr
        return frame_bgr[crop_y1:crop_y2, crop_x1:crop_x2].copy()

    def _create_violation_evidence(
        self,
        frame_bgr,
        bbox_xyxy: list[float],
        *,
        frame_timestamp_utc_ms: int,
        vehicle_id: int,
        lane_id: int,
        violation: str,
    ) -> Optional[str]:
        try:
            evidence_bgr = self._crop_violation_evidence(frame_bgr, bbox_xyxy)
            return save_evidence_image(
                self.repo_root,
                camera_id=self.camera_id,
                timestamp_utc_ms=frame_timestamp_utc_ms,
                vehicle_id=vehicle_id,
                lane_id=lane_id,
                violation=violation,
                image_bgr=evidence_bgr,
                jpeg_quality=self._evidence_jpeg_quality,
            )
        except Exception as exc:
            self.on_log(f"[{self.camera_id}] failed to save evidence image for vehicle {vehicle_id}: {exc}")
            return None

    def _save_event_to_db(self, event: ViolationEvent) -> None:
        with self._db_session_factory() as session:
            event.id = insert_violation(session, event)
