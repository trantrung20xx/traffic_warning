from __future__ import annotations

import asyncio
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

import cv2

from app.core.config import CameraConfig, RuntimeCameraLaneConfig
from app.core.evidence_images import build_evidence_image_url, save_evidence_image
from app.db.repository import (
    insert_violation,
    query_violation_payloads_by_ids,
    update_pending_violation_plate,
    update_violation_evidence_image_if_better,
)
from app.logic.lane_logic import LaneLogic, TemporalLaneAssigner
from app.logic.license_plate_logic import LicensePlateSnapshot, LicensePlateTemporalResolver
from app.logic.track_id_logic import StableTrackIdAssigner
from app.logic.vehicle_type_logic import TemporalVehicleTypeAssigner
from app.logic.direction_logic import DirectionDetectionSettings
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
from app.vision.license_plate_detector import YoloV8LicensePlateDetector
from app.vision.license_plate_ocr import LicensePlateOcr


@dataclass(slots=True)
class _LicensePlateJob:
    # Job OCR tách riêng khỏi vòng lặp chính để giảm blocking theo từng frame.
    vehicle_id: int
    ts_dt: datetime
    frame_timestamp_utc_ms: int
    bbox_xyxy: list[float]
    vehicle_crop_bgr: Any


@dataclass(slots=True)
class _PendingViolationPlateState:
    first_pending_ts: datetime
    last_pending_ts: datetime
    pending_count: int = 1
    last_lane_id: int = 0
    has_committed_plate: bool = False
    best_plate_image_quality: float = 0.0
    best_plate_image_path: Optional[str] = None
    best_violation_image_quality: float = 0.0
    best_violation_image_path: Optional[str] = None


@dataclass(slots=True)
class _TrackContinuityState:
    first_seen_ts: datetime
    last_seen_ts: datetime
    last_bbox_xyxy: list[float]
    observation_count: int = 1
    dirty: bool = False


def _bbox_iou(box_a: list[float], box_b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    if inter_area <= 0.0:
        return 0.0

    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter_area
    if union <= 0.0:
        return 0.0
    return inter_area / union


def _normalized_center_distance(box_a: list[float], box_b: list[float]) -> float:
    ax = (box_a[0] + box_a[2]) / 2.0
    ay = (box_a[1] + box_a[3]) / 2.0
    bx = (box_b[0] + box_b[2]) / 2.0
    by = (box_b[1] + box_b[3]) / 2.0

    aw = max(1.0, box_a[2] - box_a[0])
    ah = max(1.0, box_a[3] - box_a[1])
    bw = max(1.0, box_b[2] - box_b[0])
    bh = max(1.0, box_b[3] - box_b[1])
    scale = max((aw + bw) / 2.0, (ah + bh) / 2.0, 1.0)
    return (((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5) / scale


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
        detector_weights_path: str = "yolov8n.pt",
        detector_device: str = "auto",
        detector_conf_threshold: float = 0.35,
        detector_iou_threshold: float = 0.7,
        detector_allowed_classes: Optional[list[str]] = None,
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
        turn_evidence_turn_zone_hit_weight: float = 2.1,
        turn_evidence_exit_zone_hit_weight: float = 4.1,
        turn_evidence_exit_line_hit_weight: float = 5.2,
        turn_evidence_heading_support_weight: float = 1.3,
        turn_evidence_curvature_support_weight: float = 0.7,
        turn_evidence_opposite_direction_weight: float = 2.0,
        turn_evidence_temporal_bonus_weight: float = 0.4,
        turn_evidence_no_signal_penalty: float = 0.35,
        turn_evidence_temporal_hits_min: int = 2,
        turn_evidence_strong_exit_min_temporal_hits: int = 2,
        turn_evidence_strong_exit_min_turn_zone_hits: int = 2,
        turn_score_threshold: float = 4.2,
        turn_score_threshold_with_exit: float = 4.2,
        u_turn_score_threshold: float = 7.2,
        u_turn_score_threshold_with_exit: float = 5.0,
        straight_score_threshold: float = 4.5,
        trajectory_sample_inside_polygon_min_hits: int = 2,
        trajectory_entry_heading_lookback_points: int = 4,
        trajectory_entry_heading_min_displacement_px: float = 8.0,
        trajectory_heading_local_window_points: int = 3,
        lane_fallback_reference_sample_window: int = 32,
        lane_fallback_reference_min_samples: int = 3,
        lane_fallback_reference_consensus_min: float = 0.78,
        lane_fallback_reference_inlier_dot_min: float = 0.60,
        lane_fallback_reference_inlier_ratio_min: float = 0.78,
        lane_fallback_reference_max_age_ms: int = 180000,
        lane_fallback_reference_trajectory_blend_max_weight: float = 0.35,
        lane_fallback_reference_trajectory_blend_min_alignment_dot: float = 0.35,
        direction_detection_settings: Optional[DirectionDetectionSettings] = None,
        state_prune_max_age_s: float = 60.0,
        rtsp_reconnect_delay_s: float = 2.0,
        preview_max_fps: float = 15.0,
        preview_jpeg_quality: int = 75,
        preview_output_width: int = 1280,
        preview_output_height: int = 720,
        processing_fps_window_s: float = 1.5,
        processing_prune_interval_ms: int = 700,
        license_plate_worker_max_pending_jobs: int = 64,
        license_plate_worker_batch_size: int = 8,
        evidence_crop_expand_x_ratio: float = 0.28,
        evidence_crop_expand_y_top_ratio: float = 0.32,
        evidence_crop_expand_y_bottom_ratio: float = 0.27,
        evidence_crop_min_size_px: int = 24,
        evidence_jpeg_quality: int = 92,
        detector_backend: str = "pytorch",
        license_plate_enabled: bool = False,
        license_plate_detector_weights_path: str = "backend/license_plate_yolov8.pt",
        license_plate_detector_conf_threshold: float = 0.35,
        license_plate_detector_allowed_classes: Optional[list[str]] = None,
        license_plate_detector_backend: str = "pytorch",
        license_plate_ocr_backend: str = "paddleocr",
        license_plate_easyocr_lang: str = "en",
        license_plate_easyocr_use_gpu: bool = False,
        license_plate_paddle_ocr_version: str = "PP-OCRv5",
        license_plate_paddle_text_detection_model_name: str = "PP-OCRv5_mobile_det",
        license_plate_paddle_text_recognition_model_name: str = "PP-OCRv5_mobile_rec",
        license_plate_paddle_lang: str = "en",
        license_plate_paddle_use_gpu: bool = False,
        license_plate_read_interval_ms: int = 500,
        license_plate_min_ocr_confidence: float = 0.65,
        license_plate_consensus_min_hits: int = 2,
        license_plate_candidate_window_ms: int = 4000,
        license_plate_max_attempts_before_unreadable: int = 6,
        license_plate_crop_expand_x_ratio: float = 0.10,
        license_plate_crop_expand_y_ratio: float = 0.08,
        license_plate_image_jpeg_quality: int = 92,
        license_plate_violation_update_enabled: bool = True,
        license_plate_violation_update_min_confidence: float = 0.8,
        license_plate_violation_update_consensus_min_hits: int = 2,
        license_plate_violation_update_window_ms: int = 5000,
        license_plate_violation_require_clean_track: bool = True,
        license_plate_prioritize_pending_violation_ocr: bool = True,
        license_plate_violation_track_max_gap_ms: int = 900,
        license_plate_violation_track_min_observations: int = 3,
        license_plate_violation_track_max_center_jump: float = 1.8,
        license_plate_violation_track_overlap_risk_iou: float = 0.45,
        license_plate_violation_track_overlap_risk_distance: float = 0.55,
    ):
        # Root dùng để lưu evidence/config theo camera.
        self.repo_root = Path(repo_root)
        # Snapshot cấu hình camera (RTSP, location, frame size...).
        self.camera_config = camera_config
        # Lane config đã denormalize về pixel để dùng trực tiếp trong runtime.
        self.lane_config = lane_config

        # Callback đẩy track realtime lên manager/websocket layer.
        self.on_track = on_track
        # Callback đẩy vi phạm realtime lên manager/websocket layer.
        self.on_violation = on_violation

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
            inference_backend=detector_backend,
            device=detector_device,
            conf_threshold=detector_conf_threshold,
            iou_threshold=detector_iou_threshold,
            allowed_classes=detector_allowed_classes,
        )
        self.tracker = YoloByteTrackVehicleTracker(self.detector, tracker_config=tracker_config)
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
            evidence_weight_turn_zone=turn_evidence_turn_zone_hit_weight,
            evidence_weight_exit_zone=turn_evidence_exit_zone_hit_weight,
            evidence_weight_exit_line=turn_evidence_exit_line_hit_weight,
            evidence_weight_heading_support=turn_evidence_heading_support_weight,
            evidence_weight_curvature_support=turn_evidence_curvature_support_weight,
            evidence_weight_opposite_direction=turn_evidence_opposite_direction_weight,
            evidence_weight_temporal_bonus=turn_evidence_temporal_bonus_weight,
            evidence_penalty_no_signal=turn_evidence_no_signal_penalty,
            evidence_temporal_hits_min=turn_evidence_temporal_hits_min,
            evidence_strong_exit_min_temporal_hits=turn_evidence_strong_exit_min_temporal_hits,
            evidence_strong_exit_min_turn_zone_hits=turn_evidence_strong_exit_min_turn_zone_hits,
            threshold_turn_score=turn_score_threshold,
            threshold_turn_score_with_exit=turn_score_threshold_with_exit,
            threshold_u_turn_score=u_turn_score_threshold,
            threshold_u_turn_score_with_exit=u_turn_score_threshold_with_exit,
            threshold_straight_score=straight_score_threshold,
            trajectory_sample_inside_min_hits=trajectory_sample_inside_polygon_min_hits,
            trajectory_entry_heading_lookback_points=trajectory_entry_heading_lookback_points,
            trajectory_entry_heading_min_displacement_px=trajectory_entry_heading_min_displacement_px,
            trajectory_heading_local_window_points=trajectory_heading_local_window_points,
            lane_fallback_reference_sample_window=lane_fallback_reference_sample_window,
            lane_fallback_reference_min_samples=lane_fallback_reference_min_samples,
            lane_fallback_reference_consensus_min=lane_fallback_reference_consensus_min,
            lane_fallback_reference_inlier_dot_min=lane_fallback_reference_inlier_dot_min,
            lane_fallback_reference_inlier_ratio_min=lane_fallback_reference_inlier_ratio_min,
            lane_fallback_reference_max_age_ms=lane_fallback_reference_max_age_ms,
            lane_fallback_reference_trajectory_blend_max_weight=lane_fallback_reference_trajectory_blend_max_weight,
            lane_fallback_reference_trajectory_blend_min_alignment_dot=(
                lane_fallback_reference_trajectory_blend_min_alignment_dot
            ),
            direction_detection_settings=direction_detection_settings,
        )

        self.stats = StatisticsEngine()
        # Session id phân biệt các vòng đời context khác nhau của cùng camera_id.
        self.track_session_id = f"{self.camera_id}-{uuid4().hex[:12]}"

        # Cụm state OCR biển số theo từng camera.
        self._license_plate_enabled = bool(license_plate_enabled)
        self._license_plate_last_read_ms: dict[int, int] = {}
        # Áp ngưỡng tối thiểu 100ms để tránh cấu hình quá nhỏ gây quá tải OCR.
        self._license_plate_read_interval_ms = max(int(license_plate_read_interval_ms), 100)
        # Xe đang có violation pending được OCR dày hơn để enrich nhanh nhưng vẫn giới hạn CPU.
        self._license_plate_pending_read_interval_ms = max(
            min(self._license_plate_read_interval_ms, 180),
            100,
        )
        self._license_plate_min_ocr_confidence = max(float(license_plate_min_ocr_confidence), 0.0)
        self._license_plate_ocr_detection_scan_limit = 3
        self._license_plate_crop_expand_x_ratio = float(license_plate_crop_expand_x_ratio)
        self._license_plate_crop_expand_y_ratio = float(license_plate_crop_expand_y_ratio)
        self._license_plate_image_jpeg_quality = int(license_plate_image_jpeg_quality)
        self._license_plate_violation_update_enabled = bool(license_plate_violation_update_enabled)
        self._license_plate_violation_update_min_confidence = float(
            license_plate_violation_update_min_confidence
        )
        self._license_plate_violation_update_consensus_min_hits = max(
            int(license_plate_violation_update_consensus_min_hits),
            1,
        )
        self._license_plate_violation_update_window_ms = max(
            int(license_plate_violation_update_window_ms),
            200,
        )
        self._license_plate_violation_require_clean_track = bool(
            license_plate_violation_require_clean_track
        )
        self._license_plate_prioritize_pending_violation_ocr = bool(
            license_plate_prioritize_pending_violation_ocr
        )
        self._license_plate_violation_track_max_gap_ms = max(
            int(license_plate_violation_track_max_gap_ms),
            100,
        )
        self._license_plate_violation_track_min_observations = max(
            int(license_plate_violation_track_min_observations),
            1,
        )
        self._license_plate_violation_track_max_center_jump = max(
            float(license_plate_violation_track_max_center_jump),
            0.1,
        )
        self._license_plate_violation_track_overlap_risk_iou = max(
            float(license_plate_violation_track_overlap_risk_iou),
            0.0,
        )
        self._license_plate_violation_track_overlap_risk_distance = max(
            float(license_plate_violation_track_overlap_risk_distance),
            0.0,
        )
        self._late_plate_state_lock = threading.Lock()
        self._pending_violation_plate_states: dict[int, _PendingViolationPlateState] = {}
        self._pending_violation_vehicle_ids: set[int] = set()
        self._track_continuity_states: dict[int, _TrackContinuityState] = {}
        self._license_plate_detector: Optional[YoloV8LicensePlateDetector] = None
        self._license_plate_ocr: Optional[LicensePlateOcr] = None
        self._license_plate_resolver: Optional[LicensePlateTemporalResolver] = None

        if self._license_plate_enabled:
            # Resolver giữ state OCR theo vehicle_id để vote nhiều lần đọc thành 1 kết quả ổn định.
            self._license_plate_resolver = LicensePlateTemporalResolver(
                candidate_window_ms=license_plate_candidate_window_ms,
                min_ocr_confidence=license_plate_min_ocr_confidence,
                consensus_min_hits=license_plate_consensus_min_hits,
                max_attempts_before_unreadable=license_plate_max_attempts_before_unreadable,
            )
            try:
                self._license_plate_detector = YoloV8LicensePlateDetector(
                    weights_path=license_plate_detector_weights_path,
                    inference_backend=license_plate_detector_backend,
                    conf_threshold=license_plate_detector_conf_threshold,
                    iou_threshold=detector_iou_threshold,
                    device=detector_device,
                    allowed_classes=license_plate_detector_allowed_classes,
                )
            except Exception:
                # Detector/OCR biển số lỗi khởi tạo thì degrade an toàn: tắt nhánh biển số,
                # pipeline vi phạm chính vẫn chạy.
                self._license_plate_enabled = False
                self._license_plate_detector = None
                self._license_plate_ocr = None
                self._license_plate_resolver = None
            else:
                self._license_plate_ocr = LicensePlateOcr(
                    backend=license_plate_ocr_backend,
                    easyocr_lang=license_plate_easyocr_lang,
                    easyocr_use_gpu=license_plate_easyocr_use_gpu,
                    paddle_ocr_version=license_plate_paddle_ocr_version,
                    paddle_text_detection_model_name=license_plate_paddle_text_detection_model_name,
                    paddle_text_recognition_model_name=license_plate_paddle_text_recognition_model_name,
                    paddle_lang=license_plate_paddle_lang,
                    paddle_use_gpu=license_plate_paddle_use_gpu,
                )
                if not self._license_plate_ocr.available:
                    # OCR backend không khả dụng thì tắt hẳn nhánh biển số để tránh gọi null engine.
                    self._license_plate_enabled = False
                    self._license_plate_detector = None
                    self._license_plate_ocr = None
                    self._license_plate_resolver = None

        self._db_session_factory = db_session_factory

        # Giới hạn tần suất đẩy track lên WebSocket để giảm tải cho frontend.
        self._last_track_push_ts_ms: int = 0
        self._track_push_interval_ms: int = int(track_push_interval_ms)
        self._state_prune_max_age_s: float = float(state_prune_max_age_s)
        # Hàng đợi timestamp frame đã xử lý dùng cho công thức FPS cửa sổ trượt.
        self._processed_frame_times_s: deque[float] = deque()
        self._processing_fps: Optional[float] = None
        self._stream_fps: Optional[float] = None
        self._processing_fps_window_s: float = float(processing_fps_window_s)
        # Chu kỳ prune tối thiểu 100ms để cân bằng hiệu năng và độ tươi state.
        self._prune_interval_ms: int = max(int(processing_prune_interval_ms), 100)
        self._last_prune_at_ms: int = 0

        # Lưu frame JPEG gần nhất để endpoint preview có thể phát MJPEG cho trình duyệt.
        self._preview_lock = threading.Lock()
        self._preview_condition = threading.Condition(self._preview_lock)
        self._latest_preview_jpeg: Optional[bytes] = None
        self._latest_preview_seq: int = 0
        self._last_preview_encode_ms: int = 0
        self._last_preview_source_ts_ms: int = 0
        self._preview_pending_frame_bgr = None
        self._preview_pending_frame_ts_ms: int = 0
        self._preview_pending_lock = threading.Lock()
        self._preview_pending_event = threading.Event()
        self._preview_stop_event = threading.Event()
        self._preview_jpeg_quality: int = int(preview_jpeg_quality)
        self._preview_output_width: int = max(int(preview_output_width or 0), 0)
        self._preview_output_height: int = max(int(preview_output_height or 0), 0)
        # Khoảng thời gian tối thiểu giữa hai lần encode preview.
        safe_preview_fps = max(float(preview_max_fps), 0.1)
        self._preview_min_interval_ms: int = max(int(round(1000.0 / safe_preview_fps)), 1)
        self._preview_poll_interval_s: float = max(self._preview_min_interval_ms / 1000.0, 0.01)
        self._preview_worker = threading.Thread(
            target=self._preview_worker_loop,
            name=f"preview-worker-{self.camera_id}",
            daemon=True,
        )
        # Worker preview chạy nền, tách khỏi luồng event loop realtime.
        self._preview_worker.start()

        self._license_plate_resolver_lock = threading.Lock()
        self._license_plate_worker_stop_event = threading.Event()
        # Batch size >= 1 để worker luôn có thể tiến khi có job.
        self._license_plate_worker_batch_size = max(int(license_plate_worker_batch_size), 1)
        # Giới hạn queue OCR để tránh dồn RAM khi luồng xe quá dày.
        self._license_plate_worker_max_pending_jobs = max(int(license_plate_worker_max_pending_jobs), 1)
        self._license_plate_jobs_cond = threading.Condition()
        # Pending queue theo vehicle_id: mỗi xe chỉ giữ job mới nhất để tránh backlog cũ.
        self._license_plate_pending_jobs: OrderedDict[int, _LicensePlateJob] = OrderedDict()
        self._license_plate_worker: Optional[threading.Thread] = None
        if self._license_plate_enabled:
            self._license_plate_worker = threading.Thread(
                target=self._license_plate_worker_loop,
                name=f"license-plate-worker-{self.camera_id}",
                daemon=True,
            )
            # Worker OCR biển số chạy song song để không block vòng lặp chính.
            self._license_plate_worker.start()

        # Cấu hình crop evidence tổng quan (khác với crop OCR biển số).
        self._evidence_crop_expand_x_ratio: float = float(evidence_crop_expand_x_ratio)
        self._evidence_crop_expand_y_top_ratio: float = float(evidence_crop_expand_y_top_ratio)
        self._evidence_crop_expand_y_bottom_ratio: float = float(evidence_crop_expand_y_bottom_ratio)
        self._evidence_crop_min_size_px: int = int(evidence_crop_min_size_px)
        self._evidence_jpeg_quality: int = int(evidence_jpeg_quality)

    @property
    def camera_id(self) -> str:
        return self.camera_config.camera_id

    def get_latest_preview_jpeg(self) -> Optional[bytes]:
        with self._preview_condition:
            # Trả bytes JPEG mới nhất hoặc None nếu chưa có frame nào được encode.
            return self._latest_preview_jpeg

    def get_latest_preview_snapshot(self) -> tuple[Optional[bytes], int]:
        with self._preview_condition:
            return self._latest_preview_jpeg, int(self._latest_preview_seq)

    def wait_for_preview_after(self, *, last_seq: int, timeout_s: float) -> tuple[Optional[bytes], int]:
        safe_timeout_s = max(float(timeout_s), 0.01)
        deadline = time.perf_counter() + safe_timeout_s
        with self._preview_condition:
            while not self._preview_stop_event.is_set():
                if self._latest_preview_jpeg is not None and self._latest_preview_seq > int(last_seq):
                    return self._latest_preview_jpeg, int(self._latest_preview_seq)
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return None, int(last_seq)
                self._preview_condition.wait(timeout=remaining)
            return None, int(last_seq)

    def request_shutdown(self) -> None:
        """Signal camera resources to close promptly during server shutdown."""
        # Dừng worker nền trước để tránh thread đụng vào stream đã đóng.
        self._stop_background_workers()
        try:
            self.rtsp_reader.close()
        except Exception:
            pass

    def _stop_background_workers(self) -> None:
        # Dừng mềm worker nền để shutdown không treo event loop.
        self._preview_stop_event.set()
        self._preview_pending_event.set()
        with self._preview_condition:
            self._preview_condition.notify_all()
        with self._license_plate_jobs_cond:
            self._license_plate_worker_stop_event.set()
            self._license_plate_jobs_cond.notify_all()

        preview_worker = getattr(self, "_preview_worker", None)
        if preview_worker is not None and preview_worker.is_alive():
            # Join có timeout để tránh deadlock khi shutdown.
            preview_worker.join(timeout=0.5)

        license_plate_worker = self._license_plate_worker
        if license_plate_worker is not None and license_plate_worker.is_alive():
            license_plate_worker.join(timeout=1.0)

    def _submit_preview_frame(self, frame_bgr, frame_timestamp_utc_ms: int) -> None:
        # Chỉ giữ frame mới nhất; preview không cần xử lý mọi frame như pipeline AI.
        with self._preview_pending_lock:
            self._preview_pending_frame_bgr = frame_bgr
            self._preview_pending_frame_ts_ms = int(frame_timestamp_utc_ms)
        self._preview_pending_event.set()

    def _preview_worker_loop(self) -> None:
        while not self._preview_stop_event.is_set():
            # Chờ tín hiệu có frame mới; timeout theo nhịp preview để có thể tự pull
            # trực tiếp từ reader ngay cả khi pipeline AI đang chậm.
            has_pending = self._preview_pending_event.wait(timeout=self._preview_poll_interval_s)
            if has_pending:
                # Clear event trước khi lấy frame để tránh xử lý lặp cùng một tín hiệu.
                self._preview_pending_event.clear()
            if self._preview_stop_event.is_set():
                break
            frame_bgr = None
            frame_timestamp_utc_ms = 0
            if has_pending:
                with self._preview_pending_lock:
                    # Lấy frame mới nhất và xóa pending để các frame cũ tự bị bỏ.
                    frame_bgr = self._preview_pending_frame_bgr
                    frame_timestamp_utc_ms = self._preview_pending_frame_ts_ms
                    self._preview_pending_frame_bgr = None
                    self._preview_pending_frame_ts_ms = 0
            if frame_bgr is None:
                latest = self.rtsp_reader.peek_latest()
                if latest is not None:
                    frame_bgr = latest.bgr
                    frame_timestamp_utc_ms = int(latest.timestamp_utc_ms)
            if frame_bgr is None:
                continue
            try:
                self._maybe_update_preview(
                    frame_bgr,
                    source_timestamp_utc_ms=frame_timestamp_utc_ms,
                )
            except Exception:
                continue

    def _queue_license_plate_job(
        self,
        *,
        vehicle_id: int,
        ts_dt: datetime,
        frame_timestamp_utc_ms: int,
        bbox_xyxy: list[float],
        vehicle_crop_bgr,
    ) -> None:
        # Freeze toàn bộ dữ liệu cần thiết ngay lúc enqueue để worker đọc độc lập.
        job = _LicensePlateJob(
            # Ép kiểu ngay khi enqueue để worker luôn nhận dữ liệu sạch.
            vehicle_id=int(vehicle_id),
            ts_dt=ts_dt,
            frame_timestamp_utc_ms=int(frame_timestamp_utc_ms),
            bbox_xyxy=[float(value) for value in bbox_xyxy],
            vehicle_crop_bgr=vehicle_crop_bgr,
        )
        prioritize_pending = bool(self._license_plate_prioritize_pending_violation_ocr)
        is_pending_violation_vehicle = self._requires_plate_resolution(vehicle_id=int(job.vehicle_id))
        with self._license_plate_jobs_cond:
            existing = self._license_plate_pending_jobs.get(job.vehicle_id)
            if existing is not None:
                # Cùng 1 vehicle_id thì ghi đè để OCR luôn chạy trên crop mới nhất.
                self._license_plate_pending_jobs[job.vehicle_id] = job
                # Xe đang có violation pending được đẩy lên ưu tiên xử lý sớm hơn.
                self._license_plate_pending_jobs.move_to_end(
                    job.vehicle_id,
                    last=not (prioritize_pending and is_pending_violation_vehicle),
                )
            else:
                if len(self._license_plate_pending_jobs) >= self._license_plate_worker_max_pending_jobs:
                    # Quá tải thì bỏ job cũ nhất toàn cục để bảo vệ độ trễ realtime.
                    self._license_plate_pending_jobs.popitem(last=False)
                self._license_plate_pending_jobs[job.vehicle_id] = job
                if prioritize_pending and is_pending_violation_vehicle:
                    self._license_plate_pending_jobs.move_to_end(job.vehicle_id, last=False)
            # Đánh thức worker để xử lý batch mới.
            self._license_plate_jobs_cond.notify()

    def _requires_plate_resolution(self, *, vehicle_id: int) -> bool:
        normalized_vehicle_id = int(vehicle_id)
        with self._late_plate_state_lock:
            if normalized_vehicle_id not in self._pending_violation_vehicle_ids:
                return False
            pending_state = self._pending_violation_plate_states.get(normalized_vehicle_id)
        if pending_state is None:
            # Ưu tiên không bỏ sót OCR cho xe pending khi metadata tạm thời chưa đồng bộ.
            return True
        return not bool(pending_state.has_committed_plate)

    def _dequeue_license_plate_jobs(self) -> list[_LicensePlateJob]:
        with self._license_plate_jobs_cond:
            while not self._license_plate_worker_stop_event.is_set() and not self._license_plate_pending_jobs:
                # Wait timeout ngắn để vẫn phản ứng nhanh với tín hiệu stop.
                self._license_plate_jobs_cond.wait(timeout=0.2)

            if self._license_plate_worker_stop_event.is_set():
                return []

            jobs: list[_LicensePlateJob] = []
            # Lấy batch FIFO để cân bằng giữa thông lượng và độ trễ.
            while self._license_plate_pending_jobs and len(jobs) < self._license_plate_worker_batch_size:
                _, job = self._license_plate_pending_jobs.popitem(last=False)
                jobs.append(job)
            return jobs

    def _observe_license_plate_attempt(
        self,
        *,
        vehicle_id: int,
        ts_dt: datetime,
        raw_text: Optional[str],
        confidence: Optional[float],
    ) -> None:
        resolver = self._license_plate_resolver
        if resolver is None:
            return
        with self._license_plate_resolver_lock:
            # Mỗi lần thử đọc đều được ghi nhận để state OCR có thể chuyển
            # pending -> confirmed/uncertain/unreadable theo thời gian.
            resolver.observe_attempt(
                vehicle_id=vehicle_id,
                ts=ts_dt,
                raw_text=raw_text,
                confidence=confidence,
            )

    def _extract_plate_crop_from_vehicle_crop(self, vehicle_crop_bgr, bbox_xyxy: list[float]):
        # BBox biển số từ detector là float nên cần làm tròn + clamp biên crop.
        x1, y1, x2, y2 = [float(value) for value in bbox_xyxy]
        h, w = vehicle_crop_bgr.shape[:2]
        crop_x1 = max(int(round(x1)), 0)
        crop_y1 = max(int(round(y1)), 0)
        crop_x2 = min(int(round(x2)), w)
        crop_y2 = min(int(round(y2)), h)
        # Crop rỗng hoặc âm kích thước thì bỏ ngay.
        if crop_x2 <= crop_x1 or crop_y2 <= crop_y1:
            return None
        plate_crop = vehicle_crop_bgr[crop_y1:crop_y2, crop_x1:crop_x2]
        if plate_crop is None or plate_crop.size == 0:
            return None
        # Cưỡng ngưỡng kích thước tối thiểu để tránh OCR trên patch quá nhỏ gây nhiễu.
        if plate_crop.shape[0] < 8 or plate_crop.shape[1] < 20:
            return None
        return plate_crop.copy()

    def _select_best_plate_crop_and_readout(
        self,
        *,
        vehicle_id: int,
        vehicle_crop_bgr,
        detections,
        ocr: LicensePlateOcr,
    ):
        best_readout = None
        best_plate_crop = None
        fallback_plate_crop = None
        is_pending_violation_vehicle = self._requires_plate_resolution(vehicle_id=int(vehicle_id))
        scan_limit = max(int(self._license_plate_ocr_detection_scan_limit), 1) if is_pending_violation_vehicle else 1

        for detection in list(detections)[:scan_limit]:
            plate_crop = self._extract_plate_crop_from_vehicle_crop(
                vehicle_crop_bgr,
                detection.bbox_xyxy,
            )
            if plate_crop is None:
                continue
            if fallback_plate_crop is None:
                fallback_plate_crop = plate_crop

            readout = ocr.read_best(
                plate_crop,
                aggressive=bool(is_pending_violation_vehicle),
            )
            if readout is None:
                continue
            if best_readout is None or float(readout.confidence) > float(best_readout.confidence):
                best_readout = readout
                best_plate_crop = plate_crop

            # Khi đã có kết quả rất chắc thì dừng sớm để tiết kiệm CPU.
            if float(readout.confidence) >= max(float(self._license_plate_min_ocr_confidence) + 0.18, 0.88):
                break

        if best_plate_crop is None:
            best_plate_crop = fallback_plate_crop
        return best_readout, best_plate_crop

    def _process_license_plate_jobs(self, jobs: list[_LicensePlateJob]) -> None:
        detector = self._license_plate_detector
        ocr = self._license_plate_ocr
        if detector is None or ocr is None:
            return

        # Chạy detector biển số theo batch trước, sau đó OCR từng crop plate tốt nhất.
        detection_batches = detector.detect_batch([job.vehicle_crop_bgr for job in jobs])

        for index, job in enumerate(jobs):
            self._attempt_evidence_upgrade(
                vehicle_id=job.vehicle_id,
                ts_dt=job.ts_dt,
                vehicle_crop_bgr=job.vehicle_crop_bgr,
            )
            detections = detection_batches[index] if index < len(detection_batches) else []
            if not detections:
                self._observe_license_plate_attempt(
                    vehicle_id=job.vehicle_id,
                    ts_dt=job.ts_dt,
                    raw_text=None,
                    confidence=None,
                )
                self._attempt_late_plate_enrichment(
                    vehicle_id=job.vehicle_id,
                    ts_dt=job.ts_dt,
                    plate_crop_bgr=None,
                )
                continue

            readout, plate_crop = self._select_best_plate_crop_and_readout(
                vehicle_id=job.vehicle_id,
                vehicle_crop_bgr=job.vehicle_crop_bgr,
                detections=detections,
                ocr=ocr,
            )
            if plate_crop is None:
                self._observe_license_plate_attempt(
                    vehicle_id=job.vehicle_id,
                    ts_dt=job.ts_dt,
                    raw_text=None,
                    confidence=None,
                )
                self._attempt_late_plate_enrichment(
                    vehicle_id=job.vehicle_id,
                    ts_dt=job.ts_dt,
                    plate_crop_bgr=None,
                )
                continue

            # readout có thể None nếu OCR fail; resolver sẽ tự xử lý theo luật temporal voting.
            self._observe_license_plate_attempt(
                vehicle_id=job.vehicle_id,
                ts_dt=job.ts_dt,
                raw_text=readout.text if readout is not None else None,
                confidence=readout.confidence if readout is not None else None,
            )
            self._attempt_late_plate_enrichment(
                vehicle_id=job.vehicle_id,
                ts_dt=job.ts_dt,
                plate_crop_bgr=plate_crop,
            )

    def _license_plate_worker_loop(self) -> None:
        while not self._license_plate_worker_stop_event.is_set():
            jobs = self._dequeue_license_plate_jobs()
            if not jobs:
                continue
            try:
                self._process_license_plate_jobs(jobs)
            except Exception:
                continue

    def _register_pending_violation_for_late_plate(
        self,
        *,
        vehicle_id: int,
        lane_id: int,
        ts_dt: datetime,
        bbox_xyxy: list[float],
        has_committed_plate: bool = False,
        initial_plate_image_path: Optional[str] = None,
        initial_violation_image_path: Optional[str] = None,
        initial_violation_image_quality: float = 0.0,
    ) -> None:
        if not self._license_plate_violation_update_enabled:
            return
        if not self._license_plate_enabled:
            return

        normalized_vehicle_id = int(vehicle_id)
        normalized_lane_id = int(lane_id)
        normalized_bbox = [float(value) for value in bbox_xyxy]
        normalized_initial_plate_image_path = str(initial_plate_image_path or "").strip() or None
        normalized_initial_violation_image_path = (
            str(initial_violation_image_path or "").strip() or None
        )
        normalized_initial_violation_image_quality = max(
            float(initial_violation_image_quality),
            0.0,
        )
        with self._late_plate_state_lock:
            state = self._pending_violation_plate_states.get(normalized_vehicle_id)
            if state is None:
                self._pending_violation_plate_states[normalized_vehicle_id] = _PendingViolationPlateState(
                    first_pending_ts=ts_dt,
                    last_pending_ts=ts_dt,
                    pending_count=1,
                    last_lane_id=normalized_lane_id,
                    has_committed_plate=bool(has_committed_plate),
                    best_plate_image_path=normalized_initial_plate_image_path,
                    best_violation_image_quality=normalized_initial_violation_image_quality,
                    best_violation_image_path=normalized_initial_violation_image_path,
                )
            else:
                state.last_pending_ts = ts_dt
                state.pending_count += 1
                state.last_lane_id = normalized_lane_id
                if bool(has_committed_plate):
                    state.has_committed_plate = True
                if normalized_initial_plate_image_path and not state.best_plate_image_path:
                    state.best_plate_image_path = normalized_initial_plate_image_path
                if (
                    normalized_initial_violation_image_path
                    and (
                        state.best_violation_image_path is None
                        or normalized_initial_violation_image_quality
                        > float(state.best_violation_image_quality)
                    )
                ):
                    state.best_violation_image_path = normalized_initial_violation_image_path
                    state.best_violation_image_quality = normalized_initial_violation_image_quality

            self._pending_violation_vehicle_ids.add(normalized_vehicle_id)
            if normalized_vehicle_id not in self._track_continuity_states:
                self._track_continuity_states[normalized_vehicle_id] = _TrackContinuityState(
                    first_seen_ts=ts_dt,
                    last_seen_ts=ts_dt,
                    last_bbox_xyxy=normalized_bbox,
                    observation_count=1,
                    dirty=False,
                )

    def _clear_pending_violation_for_late_plate(
        self,
        *,
        vehicle_id: int,
        drop_license_plate_runtime: bool = False,
    ) -> None:
        normalized_vehicle_id = int(vehicle_id)
        with self._late_plate_state_lock:
            self._pending_violation_vehicle_ids.discard(normalized_vehicle_id)
            self._pending_violation_plate_states.pop(normalized_vehicle_id, None)
            self._track_continuity_states.pop(normalized_vehicle_id, None)
        self._license_plate_last_read_ms.pop(normalized_vehicle_id, None)
        if not drop_license_plate_runtime:
            return
        resolver = self._license_plate_resolver
        if resolver is None:
            return
        with self._license_plate_resolver_lock:
            resolver.discard(vehicle_id=normalized_vehicle_id)

    def _plate_crop_quality_score(self, plate_crop_bgr) -> float:
        if plate_crop_bgr is None or getattr(plate_crop_bgr, "size", 0) == 0:
            return 0.0
        try:
            height, width = plate_crop_bgr.shape[:2]
        except Exception:
            return 0.0
        if height <= 0 or width <= 0:
            return 0.0

        area_score = min((float(height * width) / 6000.0), 4.0)
        try:
            gray = cv2.cvtColor(plate_crop_bgr, cv2.COLOR_BGR2GRAY)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        except Exception:
            sharpness = 0.0
        sharpness_score = min(max(sharpness, 0.0) / 300.0, 4.0)
        return float(area_score + sharpness_score)

    def _update_late_plate_track_continuity(self, *, tracks, ts_dt: datetime) -> None:
        if not self._license_plate_violation_update_enabled:
            return
        if not self._license_plate_enabled:
            return
        if not self._license_plate_violation_require_clean_track:
            return

        with self._late_plate_state_lock:
            pending_vehicle_ids = list(self._pending_violation_vehicle_ids)
        if not pending_vehicle_ids:
            return

        track_map = {int(track.vehicle_id): track for track in tracks}

        for vehicle_id in pending_vehicle_ids:
            track = track_map.get(vehicle_id)
            if track is None:
                with self._late_plate_state_lock:
                    pending_state = self._pending_violation_plate_states.get(vehicle_id)
                    continuity_state = self._track_continuity_states.get(vehicle_id)
                if pending_state is None:
                    continue
                reference_ts = (
                    continuity_state.last_seen_ts
                    if continuity_state is not None
                    else pending_state.last_pending_ts
                )
                gap_ms = (ts_dt - reference_ts).total_seconds() * 1000.0
                if gap_ms <= float(self._license_plate_violation_track_max_gap_ms):
                    continue
                # Track đã mất đủ lâu: kết thúc phiên enrich cho vehicle này.
                self._clear_pending_violation_for_late_plate(
                    vehicle_id=vehicle_id,
                    drop_license_plate_runtime=not pending_state.has_committed_plate,
                )
                continue

            normalized_bbox = [float(value) for value in track.bbox_xyxy]
            with self._late_plate_state_lock:
                state = self._track_continuity_states.get(vehicle_id)
                if state is None:
                    self._track_continuity_states[vehicle_id] = _TrackContinuityState(
                        first_seen_ts=ts_dt,
                        last_seen_ts=ts_dt,
                        last_bbox_xyxy=normalized_bbox,
                        observation_count=1,
                        dirty=False,
                    )
                    state = self._track_continuity_states[vehicle_id]
                else:
                    gap_ms = (ts_dt - state.last_seen_ts).total_seconds() * 1000.0
                    if gap_ms > float(self._license_plate_violation_track_max_gap_ms):
                        state.dirty = True

                    jump_distance = _normalized_center_distance(normalized_bbox, state.last_bbox_xyxy)
                    if jump_distance > float(self._license_plate_violation_track_max_center_jump):
                        state.dirty = True

                    state.last_seen_ts = ts_dt
                    state.last_bbox_xyxy = normalized_bbox
                    state.observation_count += 1

            for other_track in tracks:
                other_vehicle_id = int(other_track.vehicle_id)
                if other_vehicle_id == vehicle_id:
                    continue
                other_bbox = [float(value) for value in other_track.bbox_xyxy]
                if _bbox_iou(normalized_bbox, other_bbox) >= float(
                    self._license_plate_violation_track_overlap_risk_iou
                ) or _normalized_center_distance(normalized_bbox, other_bbox) <= float(
                    self._license_plate_violation_track_overlap_risk_distance
                ):
                    with self._late_plate_state_lock:
                        state = self._track_continuity_states.get(vehicle_id)
                        if state is not None:
                            state.dirty = True
                    break

    def _is_late_plate_update_track_clean(self, *, vehicle_id: int, ts_dt: datetime) -> bool:
        if not self._license_plate_violation_require_clean_track:
            return True

        with self._late_plate_state_lock:
            pending_state = self._pending_violation_plate_states.get(int(vehicle_id))
            continuity_state = self._track_continuity_states.get(int(vehicle_id))

        if pending_state is None or continuity_state is None:
            return False
        if continuity_state.dirty:
            return False
        if continuity_state.observation_count < int(self._license_plate_violation_track_min_observations):
            return False

        age_ms = (ts_dt - pending_state.last_pending_ts).total_seconds() * 1000.0
        if age_ms > float(self._license_plate_violation_update_window_ms):
            return False
        return True

    def _save_late_plate_evidence_from_crop(
        self,
        *,
        plate_crop_bgr,
        frame_timestamp_utc_ms: int,
        vehicle_id: int,
        lane_id: int,
    ) -> Optional[str]:
        if plate_crop_bgr is None or plate_crop_bgr.size == 0:
            return None
        try:
            return save_evidence_image(
                self.repo_root,
                camera_id=self.camera_id,
                timestamp_utc_ms=int(frame_timestamp_utc_ms),
                vehicle_id=int(vehicle_id),
                lane_id=int(lane_id),
                violation="late_plate_enrichment_license_plate",
                image_bgr=plate_crop_bgr,
                jpeg_quality=self._license_plate_image_jpeg_quality,
            )
        except Exception:
            return None

    def _vehicle_evidence_quality_score(self, vehicle_crop_bgr) -> float:
        if vehicle_crop_bgr is None or getattr(vehicle_crop_bgr, "size", 0) == 0:
            return 0.0
        try:
            height, width = vehicle_crop_bgr.shape[:2]
        except Exception:
            return 0.0
        if height <= 0 or width <= 0:
            return 0.0

        area_score = min((float(height * width) / 40000.0), 6.0)
        try:
            gray = cv2.cvtColor(vehicle_crop_bgr, cv2.COLOR_BGR2GRAY)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        except Exception:
            sharpness = 0.0
        sharpness_score = min(max(sharpness, 0.0) / 350.0, 6.0)
        return float(area_score + sharpness_score)

    def _save_late_violation_evidence_from_vehicle_crop(
        self,
        *,
        vehicle_crop_bgr,
        frame_timestamp_utc_ms: int,
        vehicle_id: int,
        lane_id: int,
    ) -> Optional[str]:
        if vehicle_crop_bgr is None or getattr(vehicle_crop_bgr, "size", 0) == 0:
            return None
        try:
            evidence_bgr = vehicle_crop_bgr.copy()
            frame_h, frame_w = evidence_bgr.shape[:2]
            if frame_h > 0 and frame_w > 0:
                thickness = max(int(round(min(frame_h, frame_w) * 0.0056)), 1)
                cv2.rectangle(
                    evidence_bgr,
                    (0, 0),
                    (frame_w - 1, frame_h - 1),
                    (0, 0, 255),
                    thickness=thickness,
                    lineType=cv2.LINE_AA,
                )
            return save_evidence_image(
                self.repo_root,
                camera_id=self.camera_id,
                timestamp_utc_ms=int(frame_timestamp_utc_ms),
                vehicle_id=int(vehicle_id),
                lane_id=int(lane_id),
                violation="late_plate_enrichment_vehicle_evidence",
                image_bgr=evidence_bgr,
                jpeg_quality=self._evidence_jpeg_quality,
            )
        except Exception:
            return None

    def _emit_violation_updates_from_payloads(self, payloads: list[dict]) -> None:
        if not payloads:
            return
        for payload in payloads:
            try:
                event = ViolationEvent.model_validate(payload)
            except Exception:
                continue
            self.on_violation(event)

    def _attempt_evidence_upgrade(
        self,
        *,
        vehicle_id: int,
        ts_dt: datetime,
        vehicle_crop_bgr,
    ) -> None:
        if not self._license_plate_violation_update_enabled:
            return

        with self._late_plate_state_lock:
            pending_state = self._pending_violation_plate_states.get(int(vehicle_id))
            is_pending = int(vehicle_id) in self._pending_violation_vehicle_ids
        if pending_state is None or not is_pending:
            return
        if not self._is_late_plate_update_track_clean(vehicle_id=int(vehicle_id), ts_dt=ts_dt):
            return
        if vehicle_crop_bgr is None or getattr(vehicle_crop_bgr, "size", 0) == 0:
            return

        evidence_candidate_quality = self._vehicle_evidence_quality_score(vehicle_crop_bgr)
        evidence_quality_delta_min = 0.45
        should_upgrade_evidence = bool(
            pending_state.best_violation_image_path is None
            or (
                evidence_candidate_quality
                > (
                    float(pending_state.best_violation_image_quality)
                    + evidence_quality_delta_min
                )
            )
        )
        if not should_upgrade_evidence:
            return

        frame_timestamp_utc_ms = int(ts_dt.timestamp() * 1000.0)
        evidence_image_path = self._save_late_violation_evidence_from_vehicle_crop(
            vehicle_crop_bgr=vehicle_crop_bgr,
            frame_timestamp_utc_ms=frame_timestamp_utc_ms,
            vehicle_id=int(vehicle_id),
            lane_id=int(pending_state.last_lane_id),
        )
        if not evidence_image_path:
            return

        violation_not_before_ts = ts_dt - timedelta(
            milliseconds=int(self._license_plate_violation_update_window_ms)
        )
        updated_payloads: list[dict] = []
        with self._db_session_factory() as session:
            updated_violation_ids: list[int] = []
            updated_rows = update_violation_evidence_image_if_better(
                session,
                camera_id=self.camera_id,
                track_session_id=self.track_session_id,
                vehicle_id=int(vehicle_id),
                evidence_image_path=evidence_image_path,
                violation_not_before_ts=violation_not_before_ts,
                updated_violation_ids_out=updated_violation_ids,
            )
            if updated_rows > 0:
                updated_payloads = query_violation_payloads_by_ids(
                    session,
                    violation_ids=updated_violation_ids,
                )

        if updated_rows <= 0:
            return

        with self._late_plate_state_lock:
            state = self._pending_violation_plate_states.get(int(vehicle_id))
            if state is not None:
                state.best_violation_image_quality = max(
                    float(state.best_violation_image_quality),
                    float(evidence_candidate_quality),
                )
                state.best_violation_image_path = str(evidence_image_path)
                state.last_pending_ts = ts_dt
        self._emit_violation_updates_from_payloads(updated_payloads)

    def _attempt_late_plate_enrichment(
        self,
        *,
        vehicle_id: int,
        ts_dt: datetime,
        plate_crop_bgr,
    ) -> None:
        if not self._license_plate_violation_update_enabled:
            return
        if not self._license_plate_enabled:
            return

        with self._late_plate_state_lock:
            pending_state = self._pending_violation_plate_states.get(int(vehicle_id))
            is_pending = int(vehicle_id) in self._pending_violation_vehicle_ids
        if pending_state is None or not is_pending:
            return

        snapshot = self._license_plate_snapshot_for(vehicle_id=int(vehicle_id))
        if snapshot is None:
            return
        if snapshot.status != "confirmed":
            return
        if not snapshot.license_plate:
            return
        if snapshot.confidence is None:
            return
        if float(snapshot.confidence) < float(self._license_plate_violation_update_min_confidence):
            return
        if int(snapshot.consensus_hits) < int(self._license_plate_violation_update_consensus_min_hits):
            return
        if not self._is_late_plate_update_track_clean(vehicle_id=int(vehicle_id), ts_dt=ts_dt):
            return

        frame_timestamp_utc_ms = int(ts_dt.timestamp() * 1000.0)
        has_plate_crop = plate_crop_bgr is not None and getattr(plate_crop_bgr, "size", 0) > 0
        plate_candidate_quality = self._plate_crop_quality_score(plate_crop_bgr) if has_plate_crop else 0.0
        plate_quality_delta_min = 0.35
        existing_plate_image_path = str(pending_state.best_plate_image_path or "").strip() or None
        should_update_plate_image = bool(
            has_plate_crop
            and (
                not pending_state.has_committed_plate
                or existing_plate_image_path is None
                or plate_candidate_quality
                > (float(pending_state.best_plate_image_quality) + plate_quality_delta_min)
            )
        )
        license_plate_image_path: Optional[str] = None
        if should_update_plate_image:
            license_plate_image_path = self._save_late_plate_evidence_from_crop(
                plate_crop_bgr=plate_crop_bgr,
                frame_timestamp_utc_ms=frame_timestamp_utc_ms,
                vehicle_id=int(vehicle_id),
                lane_id=int(pending_state.last_lane_id),
            )
            if not license_plate_image_path:
                # Lưu crop mới thất bại: fallback ảnh đã commit trước đó (nếu có),
                # không mở rộng update nếu chưa từng có plate image hợp lệ.
                license_plate_image_path = existing_plate_image_path
                should_update_plate_image = False
        else:
            license_plate_image_path = existing_plate_image_path
        if not license_plate_image_path:
            return

        if not should_update_plate_image and pending_state.has_committed_plate:
            return

        violation_not_before_ts = ts_dt - timedelta(
            milliseconds=int(self._license_plate_violation_update_window_ms)
        )
        updated_payloads: list[dict] = []
        with self._db_session_factory() as session:
            updated_violation_ids: list[int] = []
            updated_rows = update_pending_violation_plate(
                session,
                camera_id=self.camera_id,
                track_session_id=self.track_session_id,
                vehicle_id=int(vehicle_id),
                license_plate=str(snapshot.license_plate),
                license_plate_status="confirmed",
                license_plate_confidence=float(snapshot.confidence),
                license_plate_image_path=license_plate_image_path,
                min_confidence=float(self._license_plate_violation_update_min_confidence),
                allowed_current_statuses=("pending", "unreadable"),
                violation_not_before_ts=violation_not_before_ts,
                updated_violation_ids_out=updated_violation_ids,
            )
            if updated_rows > 0:
                updated_payloads = query_violation_payloads_by_ids(
                    session,
                    violation_ids=updated_violation_ids,
                )

        if updated_rows > 0:
            with self._late_plate_state_lock:
                state = self._pending_violation_plate_states.get(int(vehicle_id))
                if state is not None:
                    state.has_committed_plate = True
                    if should_update_plate_image and license_plate_image_path:
                        state.best_plate_image_quality = max(
                            float(state.best_plate_image_quality),
                            float(plate_candidate_quality),
                        )
                        state.best_plate_image_path = str(license_plate_image_path)
                    state.last_pending_ts = ts_dt
            self._emit_violation_updates_from_payloads(updated_payloads)

    def _prune_late_plate_state(self, *, current_ts: datetime) -> None:
        if not self._license_plate_violation_update_enabled:
            return
        if not self._license_plate_enabled:
            return

        cutoff_ts = current_ts.timestamp() - (
            float(self._license_plate_violation_update_window_ms) / 1000.0
        )
        stale_vehicle_ids: list[tuple[int, bool]] = []
        with self._late_plate_state_lock:
            for vehicle_id, pending_state in self._pending_violation_plate_states.items():
                if pending_state.last_pending_ts.timestamp() < cutoff_ts:
                    stale_vehicle_ids.append(
                        (
                            int(vehicle_id),
                            bool(pending_state.has_committed_plate),
                        )
                    )
        for vehicle_id, has_committed_plate in stale_vehicle_ids:
            self._clear_pending_violation_for_late_plate(
                vehicle_id=vehicle_id,
                drop_license_plate_runtime=not has_committed_plate,
            )

    def _maybe_update_preview(self, frame_bgr, *, source_timestamp_utc_ms: int = 0) -> None:
        """Mã hóa JPEG theo nhịp giới hạn để giao diện web xem được ảnh camera trực tiếp."""
        source_ts = int(source_timestamp_utc_ms or 0)
        if source_ts > 0 and source_ts == self._last_preview_source_ts_ms:
            # Không encode lại cùng một frame gốc để tiết kiệm CPU.
            return
        now = int(time.time() * 1000)
        # Gate theo khoảng thời gian để giữ tốc độ encode ổn định.
        if now - self._last_preview_encode_ms < self._preview_min_interval_ms:
            return
        preview_frame = frame_bgr
        target_width = self._preview_output_width
        target_height = self._preview_output_height
        if target_width > 0 and target_height > 0:
            src_height, src_width = frame_bgr.shape[:2]
            if src_width > target_width or src_height > target_height:
                scale = min(target_width / max(src_width, 1), target_height / max(src_height, 1))
                next_width = max(int(round(src_width * scale)), 1)
                next_height = max(int(round(src_height * scale)), 1)
                if next_width != src_width or next_height != src_height:
                    preview_frame = cv2.resize(
                        frame_bgr,
                        (next_width, next_height),
                        interpolation=cv2.INTER_AREA,
                    )
        ok, buf = cv2.imencode(
            ".jpg",
            preview_frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), self._preview_jpeg_quality],
        )
        if not ok:
            return
        self._last_preview_encode_ms = now
        if source_ts > 0:
            self._last_preview_source_ts_ms = source_ts
        data = buf.tobytes()
        with self._preview_condition:
            # Luôn ghi đè ảnh preview cũ bằng ảnh mới nhất.
            self._latest_preview_jpeg = data
            self._latest_preview_seq += 1
            self._preview_condition.notify_all()

    async def run_forever(self, *, stop_event: asyncio.Event) -> None:
        """Vòng lặp xử lý liên tục cho một camera cho đến khi nhận tín hiệu dừng."""
        try:
            while not stop_event.is_set():
                # only_new=True: chỉ lấy frame mới nhất, tránh backlog khi xử lý chậm.
                frame = self.rtsp_reader.read(only_new=True)
                if frame is None:
                    # Không có frame mới: sleep ngắn để tránh vòng lặp busy-wait.
                    await asyncio.sleep(0.01)
                    continue

                # Đẩy frame sang worker preview để encode JPEG phục vụ endpoint /preview.
                self._submit_preview_frame(frame.bgr, frame.timestamp_utc_ms)
                self._stream_fps = self.rtsp_reader.get_source_fps()

                # ts_dt là timestamp chuẩn UTC dùng đồng nhất cho DB, WebSocket và state prune.
                ts_dt = datetime.fromtimestamp(frame.timestamp_utc_ms / 1000.0, tz=timezone.utc)
                # Các bước nặng CPU/GPU chạy qua thread để không block event loop FastAPI.
                tracks = await asyncio.to_thread(self.tracker.track, frame.bgr)
                tracks = await asyncio.to_thread(self.stable_track_id_assigner.assign, raw_tracks=tracks, ts=ts_dt)
                self._update_late_plate_track_continuity(tracks=tracks, ts_dt=ts_dt)
                # OCR snapshot chạy bất đồng bộ; tại frame hiện tại dùng snapshot đã hội tụ tới thời điểm này.
                plate_snapshots = self._prepare_license_plate_snapshots_and_enqueue(
                    frame.bgr,
                    tracks,
                    ts_dt=ts_dt,
                    frame_timestamp_utc_ms=frame.timestamp_utc_ms,
                )

                vehicles: list[TrackVehicle] = []
                violation_candidates: list[tuple[int, str, dict, list[float]]] = []

                for tr in tracks:
                    # 1) Làm mượt vehicle_type theo thời gian để giảm nhảy nhãn detector.
                    vehicle_type = self.temporal_vehicle_type_assigner.resolve_type(
                        vehicle_id=tr.vehicle_id,
                        predicted_type=tr.vehicle_type,
                        confidence=tr.confidence,
                        ts=ts_dt,
                    )
                    current_stable_lane_id = self.temporal_lane_assigner.get_stable_lane(vehicle_id=tr.vehicle_id)
                    lane_observation = self.lane_logic.observe_lane_from_bbox_xyxy(
                        tr.bbox_xyxy,
                        # Ưu tiên lane ổn định hiện tại để giảm lane drift khi vùng chồng lấn.
                        preferred_lane_id=current_stable_lane_id,
                    )
                    raw_lane_id = lane_observation.raw_lane_id
                    # 2) Làm mượt lane_id bằng cơ chế majority + hysteresis.
                    lane_id = self.temporal_lane_assigner.resolve_lane(
                        vehicle_id=tr.vehicle_id,
                        ts=ts_dt,
                        observation=lane_observation,
                    )
                    plate_snapshot = plate_snapshots.get(tr.vehicle_id)
                    vehicles.append(
                        TrackVehicle(
                            vehicle_id=tr.vehicle_id,
                            vehicle_type=vehicle_type,
                            lane_id=lane_id,
                            raw_lane_id=raw_lane_id,
                            license_plate=plate_snapshot.license_plate if plate_snapshot else None,
                            license_plate_status=plate_snapshot.status if plate_snapshot else None,
                            license_plate_confidence=plate_snapshot.confidence if plate_snapshot else None,
                            bbox=BBox(x1=tr.bbox_xyxy[0], y1=tr.bbox_xyxy[1], x2=tr.bbox_xyxy[2], y2=tr.bbox_xyxy[3]),
                        )
                    )

                    candidates = self.violation_logic.update_and_maybe_generate_violation(
                        vehicle_id=tr.vehicle_id,
                        vehicle_type=vehicle_type,
                        lane_id=lane_id,
                        lane_observation=lane_observation,
                        bbox_xyxy=tr.bbox_xyxy,
                        ts=ts_dt,
                    )
                    direction_status, direction_dot = self.violation_logic.get_direction_status_for_vehicle(
                        vehicle_id=tr.vehicle_id
                    )
                    # direction_* gắn vào payload xe để overlay realtime hiển thị ngược chiều tức thời.
                    vehicles[-1].direction_status = direction_status
                    vehicles[-1].direction_dot = direction_dot
                    for c in candidates:
                        # Gom candidate trước, xử lý lưu evidence/DB theo batch phía sau.
                        violation_candidates.append((tr.vehicle_id, vehicle_type, c, list(tr.bbox_xyxy)))

                if frame.timestamp_utc_ms - self._last_track_push_ts_ms >= self._track_push_interval_ms:
                    # Chỉ push theo nhịp cấu hình để websocket không bị flood.
                    self._last_track_push_ts_ms = frame.timestamp_utc_ms
                    track_msg = TrackMessage(
                        camera_id=self.camera_id,
                        timestamp=ts_dt,
                        processing_fps=self._processing_fps,
                        stream_fps=self._stream_fps,
                        vehicles=vehicles,
                    )
                    self.on_track(track_msg)

                if violation_candidates:
                    # Tách bước xử lý vi phạm ra sau vòng lặp track để giữ cấu trúc pipeline rõ ràng.
                    await self._handle_violations(
                        violation_candidates,
                        ts_dt,
                        frame_bgr=frame.bgr,
                        frame_timestamp_utc_ms=frame.timestamp_utc_ms,
                    )

                if (
                    self._last_prune_at_ms <= 0
                    or (frame.timestamp_utc_ms - self._last_prune_at_ms) >= self._prune_interval_ms
                ):
                    # Dọn state định kỳ để tránh phình bộ nhớ khi xe rời scene.
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
                    if self._license_plate_resolver is not None:
                        await asyncio.to_thread(
                            self._prune_license_plate_state,
                            ts_dt,
                        )
                    await asyncio.to_thread(
                        self._prune_late_plate_state,
                        current_ts=ts_dt,
                    )
                    # Lưu mốc prune gần nhất theo timestamp frame để tính chu kỳ lần sau.
                    self._last_prune_at_ms = int(frame.timestamp_utc_ms)
                self._mark_processed_frame()
        finally:
            # Khối finally đảm bảo worker/stream luôn được dừng dù có exception giữa vòng lặp.
            self._stop_background_workers()
            await asyncio.to_thread(self.rtsp_reader.close)

    def _license_plate_snapshot_for(self, *, vehicle_id: int) -> Optional[LicensePlateSnapshot]:
        if self._license_plate_resolver is None:
            return None
        with self._license_plate_resolver_lock:
            # Đọc snapshot dưới lock để đồng bộ với worker OCR.
            return self._license_plate_resolver.snapshot_for(vehicle_id=vehicle_id)

    def _prepare_license_plate_snapshots_and_enqueue(
        self,
        frame_bgr,
        tracks,
        *,
        ts_dt: datetime,
        frame_timestamp_utc_ms: int,
    ) -> dict[int, LicensePlateSnapshot]:
        # Trả snapshot OCR hiện có ngay trong frame hiện tại, đồng thời enqueue job mới
        # để snapshot các frame sau dần hội tụ về kết quả ổn định.
        resolver = self._license_plate_resolver
        if resolver is None:
            return {}

        snapshots: dict[int, LicensePlateSnapshot] = {}
        # Tập vehicle_id có mặt ở frame hiện tại để dọn lịch đọc stale.
        active_vehicle_ids: set[int] = set()

        for track in tracks:
            vehicle_id = int(track.vehicle_id)
            active_vehicle_ids.add(vehicle_id)
            with self._license_plate_resolver_lock:
                # touch để tránh track đang hoạt động bị prune khỏi resolver.
                resolver.touch(vehicle_id=vehicle_id, ts=ts_dt)

            if self._license_plate_enabled and self._should_attempt_license_plate_read(
                vehicle_id=vehicle_id,
                frame_timestamp_utc_ms=frame_timestamp_utc_ms,
            ):
                self._license_plate_last_read_ms[vehicle_id] = int(frame_timestamp_utc_ms)
                vehicle_crop = self._crop_vehicle_for_license_plate(frame_bgr, track.bbox_xyxy)
                if vehicle_crop is None:
                    # Không crop được vẫn tính là một attempt thất bại để resolver cập nhật trạng thái.
                    self._observe_license_plate_attempt(
                        vehicle_id=vehicle_id,
                        ts_dt=ts_dt,
                        raw_text=None,
                        confidence=None,
                    )
                else:
                    self._queue_license_plate_job(
                        vehicle_id=vehicle_id,
                        ts_dt=ts_dt,
                        frame_timestamp_utc_ms=frame_timestamp_utc_ms,
                        bbox_xyxy=list(track.bbox_xyxy),
                        vehicle_crop_bgr=vehicle_crop,
                    )

            with self._license_plate_resolver_lock:
                # Snapshot trả ngay cho payload track của frame hiện tại.
                snapshots[vehicle_id] = resolver.snapshot_for(vehicle_id=vehicle_id)

        stale_read_ids = [
            vehicle_id for vehicle_id in self._license_plate_last_read_ms.keys() if vehicle_id not in active_vehicle_ids
        ]
        # Xe không còn active thì xóa lịch throttle để tránh rò rỉ state.
        for vehicle_id in stale_read_ids:
            del self._license_plate_last_read_ms[vehicle_id]

        return snapshots

    def _should_attempt_license_plate_read(self, *, vehicle_id: int, frame_timestamp_utc_ms: int) -> bool:
        # Throttle theo từng vehicle_id để không OCR mọi frame.
        effective_interval_ms = int(self._license_plate_read_interval_ms)
        if effective_interval_ms <= 0:
            return True
        if self._requires_plate_resolution(vehicle_id=int(vehicle_id)):
            effective_interval_ms = min(
                effective_interval_ms,
                int(self._license_plate_pending_read_interval_ms),
            )
        last_read_ms = self._license_plate_last_read_ms.get(vehicle_id)
        if last_read_ms is None:
            return True
        # Chỉ đọc lại khi đã qua khoảng interval cấu hình.
        return int(frame_timestamp_utc_ms) - int(last_read_ms) >= effective_interval_ms

    def _prune_license_plate_read_schedule(self, *, current_ts: datetime) -> None:
        # Chuyển max_age từ giây sang mốc epoch để so sánh trực tiếp với timestamp_ms.
        cutoff_s = current_ts.timestamp() - self._state_prune_max_age_s
        stale_vehicle_ids = [
            vehicle_id
            for vehicle_id, ts_ms in self._license_plate_last_read_ms.items()
            if (float(ts_ms) / 1000.0) < cutoff_s
        ]
        for vehicle_id in stale_vehicle_ids:
            del self._license_plate_last_read_ms[vehicle_id]

    def _prune_license_plate_state(self, ts_dt: datetime) -> None:
        resolver = self._license_plate_resolver
        if resolver is not None:
            with self._license_plate_resolver_lock:
                # Prune resolver bằng cùng max_age với các state runtime khác.
                resolver.prune(
                    current_ts=ts_dt,
                    max_age_s=self._state_prune_max_age_s,
                )
        self._prune_license_plate_read_schedule(current_ts=ts_dt)

    def _expanded_bbox_bounds(
        self,
        frame_bgr,
        bbox_xyxy: list[float],
        *,
        expand_x_ratio: float,
        expand_y_top_ratio: float,
        expand_y_bottom_ratio: float,
    ) -> Optional[tuple[int, int, int, int]]:
        # Nới bbox theo tỉ lệ cấu hình để giữ thêm ngữ cảnh quanh xe/biển số.
        frame_height, frame_width = frame_bgr.shape[:2]
        if frame_height <= 0 or frame_width <= 0:
            return None

        # Chuẩn hóa bbox đầu vào về float để tính nhất quán giữa detector/tracker khác nhau.
        x1, y1, x2, y2 = [float(value) for value in bbox_xyxy]
        box_width = max(x2 - x1, 1.0)
        box_height = max(y2 - y1, 1.0)

        # Biên nới theo tỷ lệ bám theo kích thước đối tượng tại frame hiện tại.
        expand_x = box_width * float(expand_x_ratio)
        expand_y_top = box_height * float(expand_y_top_ratio)
        expand_y_bottom = box_height * float(expand_y_bottom_ratio)

        crop_x1 = max(int(round(x1 - expand_x)), 0)
        crop_y1 = max(int(round(y1 - expand_y_top)), 0)
        crop_x2 = min(int(round(x2 + expand_x)), frame_width)
        crop_y2 = min(int(round(y2 + expand_y_bottom)), frame_height)
        if crop_x2 <= crop_x1 or crop_y2 <= crop_y1:
            # Sau clamp mà bbox rỗng thì bỏ.
            return None

        return (crop_x1, crop_y1, crop_x2, crop_y2)

    def _crop_vehicle_for_license_plate(self, frame_bgr, bbox_xyxy: list[float]):
        bounds = self._expanded_bbox_bounds(
            frame_bgr,
            bbox_xyxy,
            expand_x_ratio=self._license_plate_crop_expand_x_ratio,
            expand_y_top_ratio=self._license_plate_crop_expand_y_ratio,
            expand_y_bottom_ratio=self._license_plate_crop_expand_y_ratio,
        )
        if bounds is None:
            return None
        crop_x1, crop_y1, crop_x2, crop_y2 = bounds
        # Crop theo thứ tự [y, x] của numpy array.
        vehicle_crop = frame_bgr[crop_y1:crop_y2, crop_x1:crop_x2]
        if vehicle_crop is None or vehicle_crop.size == 0:
            return None
        return vehicle_crop.copy()

    def _extract_license_plate_crop(self, frame_bgr, bbox_xyxy: list[float]):
        detector = self._license_plate_detector
        if detector is None:
            return None

        vehicle_crop = self._crop_vehicle_for_license_plate(frame_bgr, bbox_xyxy)
        if vehicle_crop is None:
            return None

        detections_batch = detector.detect_batch([vehicle_crop])
        detections = detections_batch[0] if detections_batch else []
        if not detections:
            return None

        # Thử nhiều bbox plate theo thứ tự confidence để tăng xác suất lấy được crop hợp lệ.
        scan_limit = max(int(self._license_plate_ocr_detection_scan_limit), 1)
        for detection in list(detections)[:scan_limit]:
            crop = self._extract_plate_crop_from_vehicle_crop(vehicle_crop, detection.bbox_xyxy)
            if crop is not None:
                return crop
        return None

    def _create_license_plate_evidence(
        self,
        frame_bgr,
        bbox_xyxy: list[float],
        *,
        frame_timestamp_utc_ms: int,
        vehicle_id: int,
        lane_id: int,
        violation: str,
    ) -> Optional[str]:
        if not self._license_plate_enabled:
            return None
        try:
            plate_crop = self._extract_license_plate_crop(frame_bgr, bbox_xyxy)
            if plate_crop is None:
                return None
            # Tên violation được suffix `_license_plate` để phân biệt với ảnh evidence tổng.
            return save_evidence_image(
                self.repo_root,
                camera_id=self.camera_id,
                timestamp_utc_ms=frame_timestamp_utc_ms,
                vehicle_id=vehicle_id,
                lane_id=lane_id,
                violation=f"{violation}_license_plate",
                image_bgr=plate_crop,
                jpeg_quality=self._license_plate_image_jpeg_quality,
            )
        except Exception:
            # Lỗi evidence biển số không làm hỏng pipeline vi phạm chính.
            return None

    def _mark_processed_frame(self) -> None:
        """Tính FPS xử lý bằng cửa sổ thời gian trượt thay vì dựa trên một frame đơn lẻ."""
        now_s = time.perf_counter()
        self._processed_frame_times_s.append(now_s)
        # Cửa sổ trượt: loại timestamp nằm ngoài khoảng [now-window, now].
        cutoff_s = now_s - self._processing_fps_window_s
        while self._processed_frame_times_s and self._processed_frame_times_s[0] < cutoff_s:
            self._processed_frame_times_s.popleft()

        if len(self._processed_frame_times_s) >= 2:
            duration_s = self._processed_frame_times_s[-1] - self._processed_frame_times_s[0]
            # FPS = số khoảng frame / tổng thời lượng cửa sổ.
            self._processing_fps = (
                (len(self._processed_frame_times_s) - 1) / duration_s if duration_s > 0 else None
            )
        else:
            # Không đủ mẫu thì để None, frontend sẽ hiểu là stale/chưa sẵn sàng.
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
        # Tạo location một lần cho cả batch violation trong cùng frame.
        violation_loc = ViolationLocation(
            road_name=camera_loc.road_name,
            intersection=camera_loc.intersection_name,
            gps_lat=camera_loc.gps_lat,
            gps_lng=camera_loc.gps_lng,
        )

        # Mỗi phần tử gồm: vehicle_id, vehicle_type, thông tin lỗi và bbox tại lúc vi phạm.
        for vehicle_id, vehicle_type, cand, bbox_xyxy in violation_candidates:
            # Tạo ảnh bằng chứng tổng quan trước khi ghi sự kiện.
            image_path = await asyncio.to_thread(
                self._create_violation_evidence,
                frame_bgr,
                bbox_xyxy,
                frame_timestamp_utc_ms=frame_timestamp_utc_ms,
                vehicle_id=vehicle_id,
                lane_id=int(cand["lane_id"]),
                violation=str(cand["violation"]),
            )
            plate_snapshot = self._license_plate_snapshot_for(vehicle_id=vehicle_id)
            # Ảnh biển số là evidence phụ, độc lập với kết quả OCR text.
            license_plate_image_path = await asyncio.to_thread(
                self._create_license_plate_evidence,
                frame_bgr,
                bbox_xyxy,
                frame_timestamp_utc_ms=frame_timestamp_utc_ms,
                vehicle_id=vehicle_id,
                lane_id=int(cand["lane_id"]),
                violation=str(cand["violation"]),
            )
            evidence_ready_for_plate = bool(str(license_plate_image_path or "").strip())
            resolved_license_plate = plate_snapshot.license_plate if plate_snapshot else None
            resolved_license_plate_status = plate_snapshot.status if plate_snapshot else None
            resolved_license_plate_confidence = plate_snapshot.confidence if plate_snapshot else None
            if (
                plate_snapshot is not None
                and plate_snapshot.license_plate
                and plate_snapshot.status == "confirmed"
                and not evidence_ready_for_plate
            ):
                # Text không có ảnh crop thì chưa đủ tiêu chuẩn bằng chứng -> giữ pending để enrich tiếp.
                resolved_license_plate = None
                resolved_license_plate_status = "pending"
                resolved_license_plate_confidence = None

            event = ViolationEvent.from_parts(
                camera_id=self.camera_id,
                location=violation_loc,
                vehicle_id=vehicle_id,
                vehicle_type=vehicle_type,
                lane_id=int(cand["lane_id"]),
                violation=str(cand["violation"]),
                image_path=image_path,
                image_url=build_evidence_image_url(image_path),
                evidence_image_path=image_path,
                evidence_image_url=build_evidence_image_url(image_path),
                license_plate=resolved_license_plate,
                license_plate_status=resolved_license_plate_status,
                license_plate_confidence=resolved_license_plate_confidence,
                license_plate_image_path=license_plate_image_path,
                license_plate_image_url=build_evidence_image_url(license_plate_image_path),
                track_session_id=self.track_session_id,
                ts=ts_dt,
            )

            # Ghi DB là thao tác đồng bộ nên chuyển sang thread để không chặn event loop.
            await asyncio.to_thread(self._save_event_to_db, event)

            initial_violation_image_quality = 0.0
            if image_path:
                initial_violation_crop = self._crop_violation_evidence(frame_bgr, bbox_xyxy)
                initial_violation_image_quality = self._vehicle_evidence_quality_score(
                    initial_violation_crop
                )
            self._register_pending_violation_for_late_plate(
                vehicle_id=vehicle_id,
                lane_id=int(cand["lane_id"]),
                ts_dt=ts_dt,
                bbox_xyxy=bbox_xyxy,
                has_committed_plate=bool(event.license_plate_status == "confirmed"),
                initial_plate_image_path=event.license_plate_image_path,
                initial_violation_image_path=image_path,
                initial_violation_image_quality=initial_violation_image_quality,
            )

            # Cập nhật thống kê realtime rồi phát sự kiện cho client đang nghe.
            self.stats.update_realtime(event)
            self.on_violation(event)

    def _crop_violation_evidence(self, frame_bgr, bbox_xyxy: list[float]):
        """Cắt vùng ảnh quanh xe vi phạm, có nới biên để giữ thêm ngữ cảnh mặt đường."""
        bounds = self._expanded_bbox_bounds(
            frame_bgr,
            bbox_xyxy,
            expand_x_ratio=self._evidence_crop_expand_x_ratio,
            expand_y_top_ratio=self._evidence_crop_expand_y_top_ratio,
            expand_y_bottom_ratio=self._evidence_crop_expand_y_bottom_ratio,
        )
        if bounds is None:
            return frame_bgr.copy()
        crop_x1, crop_y1, crop_x2, crop_y2 = bounds

        if (
            crop_x2 - crop_x1 < self._evidence_crop_min_size_px
            or crop_y2 - crop_y1 < self._evidence_crop_min_size_px
        ):
            return frame_bgr.copy()
        return frame_bgr[crop_y1:crop_y2, crop_x1:crop_x2].copy()

    def _draw_violation_highlight(self, evidence_bgr, bbox_xyxy: list[float], *, origin_x: int, origin_y: int):
        if evidence_bgr is None or getattr(evidence_bgr, "size", 0) == 0:
            return evidence_bgr

        frame_h, frame_w = evidence_bgr.shape[:2]
        if frame_h <= 0 or frame_w <= 0:
            return evidence_bgr

        x1, y1, x2, y2 = [float(value) for value in bbox_xyxy]
        local_x1 = max(int(round(x1 - float(origin_x))), 0)
        local_y1 = max(int(round(y1 - float(origin_y))), 0)
        local_x2 = min(int(round(x2 - float(origin_x))), frame_w)
        local_y2 = min(int(round(y2 - float(origin_y))), frame_h)
        if local_x2 <= local_x1 or local_y2 <= local_y1:
            return evidence_bgr

        thickness = max(int(round(min(frame_h, frame_w) * 0.0056)), 1)
        cv2.rectangle(
            evidence_bgr,
            (local_x1, local_y1),
            (local_x2 - 1, local_y2 - 1),
            (0, 0, 255),
            thickness=thickness,
            lineType=cv2.LINE_AA,
        )
        return evidence_bgr

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
        """Tạo và lưu ảnh bằng chứng cho một vi phạm."""
        try:
            bounds = self._expanded_bbox_bounds(
                frame_bgr,
                bbox_xyxy,
                expand_x_ratio=self._evidence_crop_expand_x_ratio,
                expand_y_top_ratio=self._evidence_crop_expand_y_top_ratio,
                expand_y_bottom_ratio=self._evidence_crop_expand_y_bottom_ratio,
            )
            origin_x = 0
            origin_y = 0
            if bounds is not None:
                crop_x1, crop_y1, crop_x2, crop_y2 = bounds
                if (
                    crop_x2 - crop_x1 >= self._evidence_crop_min_size_px
                    and crop_y2 - crop_y1 >= self._evidence_crop_min_size_px
                ):
                    origin_x = int(crop_x1)
                    origin_y = int(crop_y1)
            evidence_bgr = self._crop_violation_evidence(frame_bgr, bbox_xyxy)
            evidence_bgr = self._draw_violation_highlight(
                evidence_bgr,
                bbox_xyxy,
                origin_x=origin_x,
                origin_y=origin_y,
            )
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
        except Exception:
            return None

    def _save_event_to_db(self, event: ViolationEvent) -> None:
        """Lưu một sự kiện vi phạm xuống cơ sở dữ liệu."""
        with self._db_session_factory() as session:
            event.id = insert_violation(session, event)

