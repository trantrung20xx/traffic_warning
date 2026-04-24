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
    Pipeline xử lý cho từng camera:
    RTSP -> YOLOv8 phát hiện xe -> ByteTrack theo dõi -> gán làn bằng polygon ->
    áp luật vi phạm -> lưu DB và đẩy realtime qua WebSocket
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
        vehicle_type_history_recency_weight_bias: float = 0.15,
        stable_track_max_idle_ms: int = 1500,
        stable_track_min_iou_for_rebind: float = 0.15,
        stable_track_max_normalized_distance: float = 1.6,
        temporal_lane_observation_window_ms: int = 1200,
        temporal_lane_min_majority_hits: int = 3,
        temporal_lane_switch_min_duration_ms: int = 700,
        lane_assignment_preferred_overlap_ratio: float = 0.8,
        lane_assignment_preferred_overlap_margin_px: float = 6.0,
        track_push_interval_ms: int = 200,
        wrong_lane_min_duration_ms: int = 1200,
        turn_region_min_hits: int = 3,
        turn_state_timeout_ms: int = 3000,
        trajectory_history_window_ms: int = 2000,
        heading_straight_max_deg: float = 32.0,
        heading_turn_min_deg: float = 18.0,
        heading_turn_max_deg: float = 155.0,
        heading_u_turn_min_change_deg: float = 110.0,
        heading_side_sign_tolerance: float = 1e-6,
        heading_value_sign_tolerance: float = 1e-5,
        heading_straight_curvature_max_for_support: float = 0.28,
        curvature_u_turn_min: float = 0.2,
        curvature_straight_max: float = 0.24,
        curvature_turn_min: float = 0.04,
        curvature_fallback_min: float = 0.02,
        opposite_direction_cos_threshold: float = -0.3,
        line_crossing_side_tolerance_px: float = 2.0,
        line_crossing_min_pre_frames: int = 2,
        line_crossing_min_post_frames: int = 2,
        line_crossing_min_displacement_px: float = 2.0,
        line_crossing_min_displacement_ratio: float = 0.02,
        line_crossing_max_gap_ms: int = 400,
        line_crossing_cooldown_ms: int = 1200,
        violation_rearm_window_ms: int = 3500,
        evidence_expire_ms: int = 1600,
        motion_window_samples: int = 8,
        turn_evidence_decay_per_frame: float = 0.18,
        turn_evidence_score_cap: float = 30.0,
        turn_evidence_corridor_hit_weight: float = 2.1,
        turn_evidence_exit_zone_hit_weight: float = 4.1,
        turn_evidence_exit_line_hit_weight: float = 5.2,
        turn_evidence_heading_support_weight: float = 1.3,
        turn_evidence_curvature_support_weight: float = 0.7,
        turn_evidence_opposite_direction_weight: float = 2.0,
        turn_evidence_temporal_bonus_weight: float = 0.4,
        turn_evidence_no_signal_penalty: float = 0.35,
        turn_evidence_temporal_hits_min: int = 2,
        turn_evidence_strong_exit_min_temporal_hits: int = 2,
        turn_evidence_strong_exit_min_corridor_hits: int = 2,
        turn_score_threshold: float = 4.2,
        turn_score_threshold_with_exit: float = 4.2,
        u_turn_score_threshold: float = 7.2,
        u_turn_score_threshold_with_exit: float = 5.0,
        straight_score_threshold: float = 4.5,
        trajectory_sample_inside_polygon_min_hits: int = 2,
        trajectory_entry_heading_lookback_points: int = 4,
        trajectory_heading_local_window_points: int = 3,
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

        # Reader luôn resize frame về đúng kích thước đã cấu hình để polygon sau khi
        # bỏ chuẩn hóa khớp tuyệt đối với hệ tọa độ đang dùng trong logic.
        self.rtsp_reader = RtspFrameReader(
            camera_config.rtsp_url,
            reconnect_delay_s=rtsp_reconnect_delay_s,
            frame_width=camera_config.frame_width,
            frame_height=camera_config.frame_height,
        )

        # Các thành phần AI và tracking chạy hoàn toàn ở backend.
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

        # Logic gán làn và phát hiện vi phạm dựa trên polygon cấu hình thủ công.
        self.lane_logic = LaneLogic(
            lane_config.lanes,
            preferred_lane_overlap_ratio=lane_assignment_preferred_overlap_ratio,
            preferred_lane_overlap_margin_px=lane_assignment_preferred_overlap_margin_px,
        )
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
            recency_weight_bias=vehicle_type_history_recency_weight_bias,
        )
        self.violation_logic = ViolationLogic(
            lane_config.lanes,
            wrong_lane_min_duration_ms=wrong_lane_min_duration_ms,
            turn_region_min_hits=turn_region_min_hits,
            turn_state_timeout_ms=turn_state_timeout_ms,
            trajectory_history_window_ms=trajectory_history_window_ms,
            heading_straight_max_deg=heading_straight_max_deg,
            heading_turn_min_deg=heading_turn_min_deg,
            heading_turn_max_deg=heading_turn_max_deg,
            heading_u_turn_min_change_deg=heading_u_turn_min_change_deg,
            heading_side_sign_tolerance=heading_side_sign_tolerance,
            heading_value_sign_tolerance=heading_value_sign_tolerance,
            heading_straight_curvature_max=heading_straight_curvature_max_for_support,
            curvature_u_turn_min=curvature_u_turn_min,
            curvature_straight_max=curvature_straight_max,
            curvature_turn_min=curvature_turn_min,
            curvature_fallback_min=curvature_fallback_min,
            opposite_direction_cos_threshold=opposite_direction_cos_threshold,
            line_crossing_side_tolerance_px=line_crossing_side_tolerance_px,
            line_crossing_min_pre_frames=line_crossing_min_pre_frames,
            line_crossing_min_post_frames=line_crossing_min_post_frames,
            line_crossing_min_displacement_px=line_crossing_min_displacement_px,
            line_crossing_min_displacement_ratio=line_crossing_min_displacement_ratio,
            line_crossing_max_gap_ms=line_crossing_max_gap_ms,
            line_crossing_cooldown_ms=line_crossing_cooldown_ms,
            violation_rearm_window_ms=violation_rearm_window_ms,
            evidence_expire_ms=evidence_expire_ms,
            motion_window_samples=motion_window_samples,
            evidence_decay_per_frame=turn_evidence_decay_per_frame,
            evidence_score_cap=turn_evidence_score_cap,
            evidence_weight_corridor=turn_evidence_corridor_hit_weight,
            evidence_weight_exit_zone=turn_evidence_exit_zone_hit_weight,
            evidence_weight_exit_line=turn_evidence_exit_line_hit_weight,
            evidence_weight_heading_support=turn_evidence_heading_support_weight,
            evidence_weight_curvature_support=turn_evidence_curvature_support_weight,
            evidence_weight_opposite_direction=turn_evidence_opposite_direction_weight,
            evidence_weight_temporal_bonus=turn_evidence_temporal_bonus_weight,
            evidence_penalty_no_signal=turn_evidence_no_signal_penalty,
            evidence_temporal_hits_min=turn_evidence_temporal_hits_min,
            evidence_strong_exit_min_temporal_hits=turn_evidence_strong_exit_min_temporal_hits,
            evidence_strong_exit_min_corridor_hits=turn_evidence_strong_exit_min_corridor_hits,
            threshold_turn_score=turn_score_threshold,
            threshold_turn_score_with_exit=turn_score_threshold_with_exit,
            threshold_u_turn_score=u_turn_score_threshold,
            threshold_u_turn_score_with_exit=u_turn_score_threshold_with_exit,
            threshold_straight_score=straight_score_threshold,
            trajectory_sample_inside_min_hits=trajectory_sample_inside_polygon_min_hits,
            trajectory_entry_heading_lookback_points=trajectory_entry_heading_lookback_points,
            trajectory_heading_local_window_points=trajectory_heading_local_window_points,
        )

        self.stats = StatisticsEngine()

        self._db_session_factory = db_session_factory

        # Giới hạn tần suất đẩy track lên WebSocket để giảm tải cho frontend.
        self._last_track_push_ts_ms: int = 0
        self._track_push_interval_ms: int = int(track_push_interval_ms)
        self._state_prune_max_age_s: float = float(state_prune_max_age_s)
        self._processed_frame_times_s: deque[float] = deque()
        self._processing_fps: Optional[float] = None
        self._processing_fps_window_s: float = float(processing_fps_window_s)

        # Lưu frame JPEG gần nhất để endpoint preview có thể phát MJPEG cho trình duyệt.
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
        Trả dữ liệu polygon ở hệ pixel để frontend vẽ trực tiếp lên canvas.
        """
        lanes = []
        for lp in self.lane_config.lanes:
            lanes.append(
                {
                    "lane_id": lp.lane_id,
                    "polygon": lp.polygon,
                    "approach_zone": lp.approach_zone,
                    "commit_gate": lp.commit_gate,
                    "commit_line": lp.commit_line,
                    "allowed_maneuvers": lp.allowed_maneuvers or [],
                    "allowed_lane_changes": lp.allowed_lane_changes or [lp.lane_id],
                    "allowed_vehicle_types": lp.allowed_vehicle_types or ["motorcycle", "car", "truck", "bus"],
                    "maneuvers": lp.maneuvers or {},
                }
            )
        return {
            "camera_id": self.camera_id,
            "frame_width": self.lane_config.frame_width,
            "frame_height": self.lane_config.frame_height,
            "lanes": lanes,
        }

    def get_recent_trajectories_for_ui(
        self,
        *,
        limit: int = 30,
        lane_id: Optional[int] = None,
        vehicle_type: Optional[str] = None,
    ) -> dict[str, Any]:
        trajectories = self.violation_logic.get_recent_trajectories(
            limit=limit,
            lane_id=lane_id,
            vehicle_type=vehicle_type,
        )
        return {
            "camera_id": self.camera_id,
            "limit": int(limit),
            "lane_id": lane_id,
            "vehicle_type": vehicle_type,
            "rows": trajectories,
        }

    def get_latest_preview_jpeg(self) -> Optional[bytes]:
        with self._preview_lock:
            return self._latest_preview_jpeg

    def request_shutdown(self) -> None:
        """Signal camera resources to close promptly during server shutdown."""
        try:
            self.rtsp_reader.close()
        except Exception:
            pass

    def _maybe_update_preview(self, frame_bgr) -> None:
        """Mã hóa JPEG theo nhịp giới hạn để giao diện web xem được ảnh camera trực tiếp."""
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
        """Vòng lặp xử lý liên tục cho một camera cho đến khi nhận tín hiệu dừng."""
        try:
            while not stop_event.is_set():
                frame = self.rtsp_reader.read(only_new=True)
                if frame is None:
                    await asyncio.sleep(0.01)
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
                    current_stable_lane_id = self.temporal_lane_assigner.get_stable_lane(vehicle_id=tr.vehicle_id)
                    raw_lane_id = self.lane_logic.assign_lane_id_from_bbox_xyxy(
                        tr.bbox_xyxy,
                        preferred_lane_id=current_stable_lane_id,
                    )
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
        """Tính FPS xử lý bằng cửa sổ thời gian trượt thay vì dựa trên một frame đơn lẻ."""
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

        # Mỗi phần tử gồm: vehicle_id, vehicle_type, thông tin lỗi và bbox tại lúc vi phạm.
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

            # Ghi DB là thao tác đồng bộ nên chuyển sang thread để không chặn event loop.
            await asyncio.to_thread(self._save_event_to_db, event)

            # Cập nhật thống kê realtime rồi phát sự kiện cho client đang nghe.
            self.stats.update_realtime(event)
            self.on_violation(event)

    def _crop_violation_evidence(self, frame_bgr, bbox_xyxy: list[float]):
        """Cắt vùng ảnh quanh xe vi phạm, có nới biên để giữ thêm ngữ cảnh mặt đường."""
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
        """Tạo và lưu ảnh bằng chứng cho một vi phạm; lỗi lưu ảnh chỉ ghi log rồi bỏ qua."""
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
        """Lưu một sự kiện vi phạm xuống cơ sở dữ liệu."""
        with self._db_session_factory() as session:
            event.id = insert_violation(session, event)
