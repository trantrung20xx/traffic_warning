from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.camera import CameraConfig

DEFAULT_DETECTOR_ALLOWED_CLASSES = ("motorcycle", "car", "truck", "bus")
ALLOWED_VEHICLE_TYPES = set(DEFAULT_DETECTOR_ALLOWED_CLASSES)
ALLOWED_MANEUVERS = {"straight", "left", "right", "u_turn"}
MANEUVER_ORDER = ("straight", "right", "left", "u_turn")


def _validate_polygon_points(value: list[list[float]], *, field_name: str) -> list[list[float]]:
    if len(value) < 3:
        raise ValueError(f"{field_name} must contain at least 3 points")
    for point in value:
        if len(point) != 2:
            raise ValueError(f"{field_name} points must be [x, y]")
        x, y = point
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError(f"{field_name} points must be normalized to [0, 1]")
    return value


def _validate_line_points(value: list[list[float]], *, field_name: str) -> list[list[float]]:
    if len(value) != 2:
        raise ValueError(f"{field_name} must contain exactly 2 points")
    for point in value:
        if len(point) != 2:
            raise ValueError(f"{field_name} points must be [x, y]")
        x, y = point
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError(f"{field_name} points must be normalized to [0, 1]")
    return value


def _normalize_string_list(value: Any, *, field_name: str) -> list[str]:
    raw_items = [value] if isinstance(value, str) else value
    try:
        iterator = iter(raw_items)
    except TypeError:
        iterator = iter([raw_items])

    normalized: list[str] = []
    for item in iterator:
        text = str(item).strip()
        if text and text not in normalized:
            normalized.append(text)
    if not normalized:
        raise ValueError(f"{field_name} must contain at least one value")
    return normalized


def _validate_polyline_points(value: list[list[float]], *, field_name: str) -> list[list[float]]:
    if len(value) < 2:
        raise ValueError(f"{field_name} must contain at least 2 points")
    for point in value:
        if len(point) != 2:
            raise ValueError(f"{field_name} points must be [x, y]")
        x, y = point
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError(f"{field_name} points must be normalized to [0, 1]")
    return value


def _normalize_allowed_maneuvers(value: Optional[list[str]]) -> Optional[list[str]]:
    if value is None:
        return value
    normalized = list(dict.fromkeys(str(item) for item in value))
    if not normalized:
        raise ValueError("allowed_maneuvers must contain at least one maneuver")
    invalid = [item for item in normalized if item not in ALLOWED_MANEUVERS]
    if invalid:
        raise ValueError(f"unsupported maneuvers: {', '.join(invalid)}")
    return normalized


class AnalyticsChartConfig(BaseModel):
    minute_granularity_max_range_hours: int = 24
    hour_granularity_max_range_days: int = 14
    day_granularity_max_range_days: int = 120
    week_granularity_max_range_days: int = 365
    minute_axis_label_interval_minutes: int = 60
    minute_axis_max_ticks: int = 8
    hour_axis_max_ticks: int = 8
    overview_axis_max_ticks: int = 7
    point_markers_max_points: int = 240

    @field_validator(
        "minute_granularity_max_range_hours",
        "hour_granularity_max_range_days",
        "day_granularity_max_range_days",
        "week_granularity_max_range_days",
        "minute_axis_label_interval_minutes",
        "minute_axis_max_ticks",
        "hour_axis_max_ticks",
        "overview_axis_max_ticks",
        "point_markers_max_points",
    )
    @classmethod
    def validate_positive_int(cls, value: int) -> int:
        if int(value) <= 0:
            raise ValueError("analytics chart config values must be positive")
        return int(value)


class LaneAssignmentOverlapConfig(BaseModel):
    preferred_lane_overlap_ratio: float = 0.8
    preferred_lane_overlap_margin_px: float = 6.0


class TurnDetectionHeadingConfig(BaseModel):
    straight_max_deg: float = 32.0
    turn_min_deg: float = 18.0
    turn_max_deg: float = 155.0
    u_turn_min_change_deg: float = 110.0
    side_sign_tolerance: float = 1e-6
    value_sign_tolerance: float = 1e-5
    straight_curvature_max_for_heading_support: float = 0.28


class TurnDetectionCurvatureConfig(BaseModel):
    u_turn_min: float = 0.2
    straight_max: float = 0.24
    turn_min: float = 0.04
    fallback_min: float = 0.02


class TurnDetectionOppositeDirectionConfig(BaseModel):
    cos_threshold: float = -0.3


class TurnDetectionFallbackReferenceConfig(BaseModel):
    sample_window: int = Field(default=32, ge=4)
    min_samples: int = Field(default=3, ge=2)
    consensus_min: float = Field(default=0.78, ge=0.0, le=1.0)
    inlier_dot_min: float = Field(default=0.60, ge=-1.0, le=1.0)
    inlier_ratio_min: float = Field(default=0.78, ge=0.0, le=1.0)
    max_age_ms: int = Field(default=180000, ge=1000)
    trajectory_blend_max_weight: float = Field(default=0.35, ge=0.0, le=1.0)
    trajectory_blend_min_alignment_dot: float = Field(default=0.35, ge=-1.0, le=1.0)


class TurnDetectionTrajectoryConfig(BaseModel):
    sample_inside_polygon_min_hits: int = 2
    entry_heading_lookback_points: int = 4
    entry_heading_min_displacement_px: float = Field(default=8.0, ge=0.1)
    heading_local_window_points: int = 3
    fallback_reference: TurnDetectionFallbackReferenceConfig = TurnDetectionFallbackReferenceConfig()


class EvidenceFusionTurnScoringConfig(BaseModel):
    decay_per_frame: float = 0.18
    score_cap: float = 30.0
    turn_zone_hit_weight: float = 2.1
    exit_zone_hit_weight: float = 4.1
    exit_line_hit_weight: float = 5.2
    heading_support_weight: float = 1.3
    curvature_support_weight: float = 0.7
    opposite_direction_weight: float = 2.0
    temporal_continuity_bonus: float = 0.4
    no_signal_penalty: float = 0.35
    temporal_hits_min: int = 2
    strong_exit_min_temporal_hits: int = 2
    strong_exit_min_turn_zone_hits: int = 2
    threshold_turn: float = 4.2
    threshold_turn_with_exit: float = 4.2
    threshold_u_turn: float = 7.2
    threshold_u_turn_with_exit: float = 5.0
    threshold_straight: float = 4.5


class MonitoringTrajectoryUiConfig(BaseModel):
    default_limit: int = 30
    min_limit: int = 10
    max_limit: int = 80
    max_points_per_vehicle: int = 48
    stale_ms: int = 1500
    min_point_distance_px: float = 1.5


class MonitoringViolationUiConfig(BaseModel):
    list_max_rows: int = 80
    highlight_duration_ms: int = 15000


class MonitoringProcessingFpsUiConfig(BaseModel):
    stale_after_ms: int = 1000
    poll_interval_ms: int = 500


class MonitoringUiConfig(BaseModel):
    trajectory: MonitoringTrajectoryUiConfig = MonitoringTrajectoryUiConfig()
    violation: MonitoringViolationUiConfig = MonitoringViolationUiConfig()
    processing_fps: MonitoringProcessingFpsUiConfig = MonitoringProcessingFpsUiConfig()


class UiConfig(BaseModel):
    monitoring: MonitoringUiConfig = MonitoringUiConfig()


class LicensePlateConfig(BaseModel):
    enabled: bool = False
    detector_weights_path: str = "backend/license_plate_yolov8.pt"
    detector_confidence_threshold: float = 0.35
    detector_allowed_classes: list[str] = Field(
        default_factory=lambda: ["license_plate", "License Plates"]
    )
    ocr_backend: str = "paddleocr"
    easyocr_lang: str = "en"
    easyocr_use_gpu: bool = False
    paddle_ocr_version: str = "PP-OCRv5"
    paddle_text_detection_model_name: str = "PP-OCRv5_mobile_det"
    paddle_text_recognition_model_name: str = "PP-OCRv5_mobile_rec"
    paddle_lang: str = "en"
    paddle_use_gpu: bool = False
    read_interval_ms: int = 500
    min_ocr_confidence: float = 0.65
    consensus_min_hits: int = 2
    candidate_window_ms: int = 4000
    max_attempts_before_unreadable: int = 6
    crop_expand_x_ratio: float = 0.10
    crop_expand_y_ratio: float = 0.08
    image_jpeg_quality: int = 92

    @field_validator("detector_allowed_classes")
    @classmethod
    def validate_detector_allowed_classes(cls, value: Any) -> list[str]:
        raw_value = ["license_plate", "License Plates"] if value is None else value
        return _normalize_string_list(
            raw_value,
            field_name="license_plate.detector_allowed_classes",
        )

    @field_validator("ocr_backend")
    @classmethod
    def validate_ocr_backend(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in {"easyocr", "paddleocr"}:
            raise ValueError("license_plate.ocr_backend must be either 'easyocr' or 'paddleocr'")
        return normalized

    @field_validator("easyocr_lang", "paddle_lang")
    @classmethod
    def validate_ocr_lang(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not normalized:
            raise ValueError("OCR language must not be empty")
        return normalized


class ManeuverConfig(BaseModel):
    enabled: bool = True
    allowed: bool = False
    turn_zone: Optional[list[list[float]]] = None
    exit_line: Optional[list[list[float]]] = None
    exit_zone: Optional[list[list[float]]] = None

    @field_validator("turn_zone", "exit_zone")
    @classmethod
    def validate_optional_polygon(cls, value: Optional[list[list[float]]], info) -> Optional[list[list[float]]]:
        if value is None:
            return value
        return _validate_polygon_points(value, field_name=info.field_name)

    @field_validator("exit_line")
    @classmethod
    def validate_exit_line(cls, value: Optional[list[list[float]]]) -> Optional[list[list[float]]]:
        if value is None:
            return value
        return _validate_line_points(value, field_name="exit_line")


class RuntimeManeuverConfig(BaseModel):
    enabled: bool = True
    allowed: bool = False
    turn_zone: Optional[list[list[float]]] = None
    exit_line: Optional[list[list[float]]] = None
    exit_zone: Optional[list[list[float]]] = None


class DirectionDetectionDefaultsConfig(BaseModel):
    same_direction_cos_threshold: float = 0.25
    opposite_direction_cos_threshold: float = -0.45
    min_duration_ms: int = 700
    min_displacement_px: float = 7.0
    min_samples: int = 3
    evaluation_window_samples: int = 12
    segment_min_displacement_px: float = 2.0
    segment_max_gap_ms: int = 450
    warmup_min_duration_ms: int = 0
    warmup_min_samples: int = 3
    opposite_consensus_min_segments: int = 2
    opposite_consensus_ratio_min: float = 0.55
    opposite_min_displacement_px: float = 10.0
    opposite_min_displacement_lane_ratio: float = 0.10
    lane_consensus_sample_window: int = 48
    lane_consensus_min_samples: int = 6
    lane_consensus_inlier_dot_min: float = 0.75
    lane_consensus_blend_weight: float = 0.28
    lane_consensus_alignment_min_dot: float = 0.20
    lane_consensus_max_age_ms: int = 180000
    trajectory_blend_weight: float = 0.16
    trajectory_blend_min_alignment_dot: float = 0.50

    @field_validator(
        "same_direction_cos_threshold",
        "opposite_direction_cos_threshold",
        "lane_consensus_inlier_dot_min",
        "lane_consensus_alignment_min_dot",
        "trajectory_blend_min_alignment_dot",
    )
    @classmethod
    def validate_cos_threshold(cls, value: float) -> float:
        parsed = float(value)
        if parsed < -1.0 or parsed > 1.0:
            raise ValueError("direction cosine thresholds must be within [-1, 1]")
        return parsed

    @field_validator(
        "min_duration_ms",
        "min_samples",
        "evaluation_window_samples",
        "segment_max_gap_ms",
        "warmup_min_samples",
        "opposite_consensus_min_segments",
        "lane_consensus_sample_window",
        "lane_consensus_min_samples",
        "lane_consensus_max_age_ms",
    )
    @classmethod
    def validate_positive_int(cls, value: int) -> int:
        parsed = int(value)
        if parsed <= 0:
            raise ValueError("direction detection integer values must be > 0")
        return parsed

    @field_validator("warmup_min_duration_ms")
    @classmethod
    def validate_non_negative_int(cls, value: int) -> int:
        parsed = int(value)
        if parsed < 0:
            raise ValueError("direction detection warmup_min_duration_ms must be >= 0")
        return parsed

    @field_validator(
        "min_displacement_px",
        "segment_min_displacement_px",
        "opposite_min_displacement_px",
    )
    @classmethod
    def validate_positive_displacement(cls, value: float) -> float:
        parsed = float(value)
        if parsed <= 0.0:
            raise ValueError("direction detection displacement values must be > 0")
        return parsed

    @field_validator(
        "opposite_consensus_ratio_min",
        "opposite_min_displacement_lane_ratio",
        "lane_consensus_blend_weight",
        "trajectory_blend_weight",
    )
    @classmethod
    def validate_unit_interval(cls, value: float) -> float:
        parsed = float(value)
        if parsed < 0.0 or parsed > 1.0:
            raise ValueError("direction detection ratio/weight values must be within [0, 1]")
        return parsed

    @model_validator(mode="after")
    def validate_threshold_order(self):
        if self.opposite_direction_cos_threshold >= self.same_direction_cos_threshold:
            raise ValueError("opposite_direction_cos_threshold must be smaller than same_direction_cos_threshold")
        if self.opposite_consensus_min_segments > self.evaluation_window_samples:
            raise ValueError("opposite_consensus_min_segments must be <= evaluation_window_samples")
        if self.lane_consensus_min_samples > self.lane_consensus_sample_window:
            raise ValueError("lane_consensus_min_samples must be <= lane_consensus_sample_window")
        return self


class DirectionRuleConfig(BaseModel):
    enabled: bool = False
    direction_path: Optional[list[list[float]]] = None
    check_zone: Optional[list[list[float]]] = None

    @field_validator("direction_path")
    @classmethod
    def validate_direction_path(cls, value: Optional[list[list[float]]]) -> Optional[list[list[float]]]:
        if value is None:
            return value
        return _validate_polyline_points(value, field_name="direction_path")

    @field_validator("check_zone")
    @classmethod
    def validate_check_zone(cls, value: Optional[list[list[float]]]) -> Optional[list[list[float]]]:
        if value is None:
            return value
        return _validate_polygon_points(value, field_name="check_zone")


class RuntimeDirectionRuleConfig(BaseModel):
    enabled: bool = False
    direction_path: Optional[list[list[float]]] = None
    check_zone: Optional[list[list[float]]] = None


class LanePolygon(BaseModel):
    lane_id: int
    # Lưu polygon theo tọa độ chuẩn hóa [0, 1] để cấu hình thủ công không bị lệch
    # khi thay đổi kích thước canvas; lúc chạy sẽ đổi lại về pixel của khung hình camera.
    polygon: list[list[float]]  # [[x,y], ...]

    # Vùng tiếp cận để khóa lane nguồn nghiệp vụ cho bài toán turn.
    approach_zone: Optional[list[list[float]]] = None

    # Vùng commit hoặc vạch commit để xác nhận xe đã bắt đầu maneuver.
    commit_gate: Optional[list[list[float]]] = None
    commit_line: Optional[list[list[float]]] = None

    # Nếu làn gốc của xe là làn này thì chỉ được phép thực hiện các hướng trong danh sách.
    allowed_maneuvers: Optional[list[str]] = None

    # Quy tắc đổi làn cho lỗi "Đi sai làn".
    # Nếu xe đi sang làn không nằm trong danh sách này thì xem là vi phạm.
    # Mặc định chỉ cho phép xe giữ nguyên làn gốc của mình.
    allowed_lane_changes: Optional[list[int]] = None

    # Các loại phương tiện được phép đi trong làn này.
    allowed_vehicle_types: Optional[list[str]] = None
    maneuvers: Optional[dict[str, ManeuverConfig]] = None
    direction_rule: Optional[DirectionRuleConfig] = None

    @field_validator("polygon")
    @classmethod
    def validate_polygon(cls, value: list[list[float]]) -> list[list[float]]:
        return _validate_polygon_points(value, field_name="lane polygon")

    @field_validator("approach_zone", "commit_gate")
    @classmethod
    def validate_optional_polygon(cls, value: Optional[list[list[float]]], info) -> Optional[list[list[float]]]:
        if value is None:
            return value
        return _validate_polygon_points(value, field_name=info.field_name)

    @field_validator("commit_line")
    @classmethod
    def validate_commit_line(cls, value: Optional[list[list[float]]]) -> Optional[list[list[float]]]:
        if value is None:
            return value
        return _validate_line_points(value, field_name="commit_line")

    @field_validator("allowed_maneuvers")
    @classmethod
    def validate_allowed_maneuvers(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        return _normalize_allowed_maneuvers(value)

    @field_validator("allowed_vehicle_types")
    @classmethod
    def validate_allowed_vehicle_types(
        cls, value: Optional[list[str]]
    ) -> Optional[list[str]]:
        if value is None:
            return value
        normalized = list(dict.fromkeys(str(item) for item in value))
        if not normalized:
            raise ValueError("allowed_vehicle_types must contain at least one vehicle type")
        invalid = [item for item in normalized if item not in ALLOWED_VEHICLE_TYPES]
        if invalid:
            raise ValueError(f"unsupported vehicle types: {', '.join(invalid)}")
        return normalized

    @field_validator("maneuvers")
    @classmethod
    def validate_maneuvers(
        cls,
        value: Optional[dict[str, ManeuverConfig]],
    ) -> Optional[dict[str, ManeuverConfig]]:
        if value is None:
            return value
        invalid = [maneuver for maneuver in value.keys() if maneuver not in ALLOWED_MANEUVERS]
        if invalid:
            raise ValueError(f"unsupported maneuver keys: {', '.join(invalid)}")
        ordered: dict[str, ManeuverConfig] = {}
        for maneuver in MANEUVER_ORDER:
            if maneuver in value:
                ordered[maneuver] = value[maneuver]
        for maneuver, config in value.items():
            if maneuver not in ordered:
                ordered[maneuver] = config
        return ordered

    @model_validator(mode="after")
    def derive_allowed_maneuvers_from_behavior(self):
        if not self.maneuvers:
            return self

        normalized: dict[str, ManeuverConfig] = {}
        for maneuver in MANEUVER_ORDER:
            cfg = self.maneuvers.get(maneuver)
            if cfg is None:
                continue
            updates: dict[str, Any] = {}
            if not bool(cfg.enabled):
                updates["allowed"] = False
            normalized[maneuver] = cfg if not updates else cfg.model_copy(update=updates)
        if normalized:
            self.maneuvers = normalized

        if self.allowed_maneuvers is None:
            allowed = [
                maneuver
                for maneuver, cfg in (self.maneuvers or {}).items()
                if bool(cfg.enabled) and bool(cfg.allowed)
            ]
            self.allowed_maneuvers = allowed
        return self


class CameraLaneConfig(BaseModel):
    camera_id: str
    frame_width: int
    frame_height: int
    lanes: list[LanePolygon]


class RuntimeLanePolygon(BaseModel):
    lane_id: int
    polygon: list[list[float]]
    approach_zone: Optional[list[list[float]]] = None
    commit_gate: Optional[list[list[float]]] = None
    commit_line: Optional[list[list[float]]] = None
    allowed_maneuvers: Optional[list[str]] = None
    allowed_lane_changes: Optional[list[int]] = None
    allowed_vehicle_types: Optional[list[str]] = None
    maneuvers: Optional[dict[str, RuntimeManeuverConfig]] = None
    direction_rule: Optional[RuntimeDirectionRuleConfig] = None


class RuntimeCameraLaneConfig(BaseModel):
    camera_id: str
    frame_width: int
    frame_height: int
    lanes: list[RuntimeLanePolygon]


class AppConfig(BaseModel):
    settings_path: Path
    config_dir: Path
    cameras_path: Path
    lane_configs_dir: Path
    background_images_dir: Path
    evidence_images_dir: Path
    db_path: Path

    # Cấu hình detector, tracker và các tham số ảnh hưởng hiệu năng xử lý.
    detector_weights_path: str = "backend/yolov8n.pt"
    detector_device: str = "auto"
    detector_conf_threshold: float = 0.28
    detector_iou_threshold: float = 0.7
    detector_allowed_classes: list[str] = Field(
        default_factory=lambda: list(DEFAULT_DETECTOR_ALLOWED_CLASSES)
    )
    tracker_config: str = "bytetrack.yaml"
    vehicle_type_history_window_ms: int = 4000
    vehicle_type_history_size: int = 12
    stable_track_max_idle_ms: int = 1500
    stable_track_min_iou_for_rebind: float = 0.15
    stable_track_max_normalized_distance: float = 1.6
    temporal_lane_observation_window_ms: int = 1200
    temporal_lane_min_majority_hits: int = 3
    temporal_lane_switch_min_duration_ms: int = 700
    lane_assignment_overlap: LaneAssignmentOverlapConfig = LaneAssignmentOverlapConfig()
    vehicle_type_history_recency_weight_bias: float = 0.15

    # Ngưỡng cho luồng realtime, phát hiện vi phạm và ảnh bằng chứng.
    track_push_interval_ms: int = 200
    websocket_listener_queue_maxsize: int = 200
    wrong_lane_min_duration_ms: int = 1200
    turn_region_min_hits: int = 3
    turn_state_timeout_ms: int = 3000
    trajectory_history_window_ms: int = 2000
    turn_detection_heading: TurnDetectionHeadingConfig = TurnDetectionHeadingConfig()
    turn_detection_curvature: TurnDetectionCurvatureConfig = TurnDetectionCurvatureConfig()
    turn_detection_opposite_direction: TurnDetectionOppositeDirectionConfig = TurnDetectionOppositeDirectionConfig()
    turn_detection_trajectory: TurnDetectionTrajectoryConfig = TurnDetectionTrajectoryConfig()
    direction_detection_defaults: DirectionDetectionDefaultsConfig = DirectionDetectionDefaultsConfig()
    line_crossing_side_tolerance_px: float = 2.0
    line_crossing_min_pre_frames: int = 2
    line_crossing_min_post_frames: int = 2
    line_crossing_min_displacement_px: float = 2.0
    line_crossing_min_displacement_ratio: float = 0.02
    line_crossing_max_gap_ms: int = 400
    line_crossing_cooldown_ms: int = 1200
    violation_rearm_window_ms: int = 3500
    evidence_expire_ms: int = 1600
    motion_window_samples: int = 8
    evidence_fusion_turn_scoring: EvidenceFusionTurnScoringConfig = EvidenceFusionTurnScoringConfig()
    state_prune_max_age_s: float = 60.0
    rtsp_reconnect_delay_s: float = 2.0
    preview_max_fps: float = 15.0
    preview_jpeg_quality: int = 75
    processing_fps_window_s: float = 1.5
    evidence_crop_expand_x_ratio: float = 0.28
    evidence_crop_expand_y_top_ratio: float = 0.32
    evidence_crop_expand_y_bottom_ratio: float = 0.27
    evidence_crop_min_size_px: int = 24
    evidence_jpeg_quality: int = 92
    license_plate: LicensePlateConfig = LicensePlateConfig()
    analytics_chart: AnalyticsChartConfig = AnalyticsChartConfig()
    ui: UiConfig = UiConfig()

    @field_validator("detector_allowed_classes")
    @classmethod
    def validate_detector_allowed_classes(cls, value: Any) -> list[str]:
        raw_value = list(DEFAULT_DETECTOR_ALLOWED_CLASSES) if value is None else value
        return _normalize_string_list(
            raw_value,
            field_name="detection.allowed_classes",
        )


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def normalize_point(point: list[float], frame_width: int, frame_height: int) -> list[float]:
    """Đưa một điểm pixel về hệ tọa độ chuẩn hóa [0, 1]."""
    x, y = point
    if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
        return [float(x), float(y)]
    return [float(x) / max(frame_width, 1), float(y) / max(frame_height, 1)]


def denormalize_point(point: list[float], frame_width: int, frame_height: int) -> list[float]:
    """Đổi một điểm chuẩn hóa về pixel theo kích thước frame đang cấu hình."""
    x, y = point
    return [float(x) * frame_width, float(y) * frame_height]


def normalize_polygon(points: list[list[float]], frame_width: int, frame_height: int) -> list[list[float]]:
    return [normalize_point(point, frame_width, frame_height) for point in points]


def denormalize_polygon(points: list[list[float]], frame_width: int, frame_height: int) -> list[list[float]]:
    return [denormalize_point(point, frame_width, frame_height) for point in points]


def normalize_optional_polygon(
    points: Optional[list[list[float]]],
    frame_width: int,
    frame_height: int,
) -> Optional[list[list[float]]]:
    if points is None:
        return None
    return normalize_polygon(points, frame_width, frame_height)


def denormalize_optional_polygon(
    points: Optional[list[list[float]]],
    frame_width: int,
    frame_height: int,
) -> Optional[list[list[float]]]:
    if points is None:
        return None
    return denormalize_polygon(points, frame_width, frame_height)


def normalize_optional_polyline(
    points: Optional[list[list[float]]],
    frame_width: int,
    frame_height: int,
) -> Optional[list[list[float]]]:
    if points is None:
        return None
    return normalize_polygon(points, frame_width, frame_height)


def _normalize_maneuver_config_payload(
    *,
    maneuver: str,
    raw_config: dict[str, Any],
    frame_width: int,
    frame_height: int,
) -> dict[str, Any]:
    del maneuver  # maneuver được giữ để tương thích chữ ký gọi hiện tại.

    turn_zone = normalize_optional_polygon(raw_config.get("turn_zone"), frame_width, frame_height)

    exit_zone = normalize_optional_polygon(raw_config.get("exit_zone"), frame_width, frame_height)

    exit_line = normalize_optional_polygon(raw_config.get("exit_line"), frame_width, frame_height)

    enabled = bool(raw_config.get("enabled", True))
    allowed = enabled and bool(raw_config.get("allowed", False))

    return {
        "enabled": enabled,
        "allowed": allowed,
        "turn_zone": turn_zone,
        "exit_zone": exit_zone,
        "exit_line": exit_line,
    }


def _normalize_lane_maneuvers_payload(
    *,
    lane_raw: dict[str, Any],
    frame_width: int,
    frame_height: int,
) -> Optional[dict[str, Any]]:
    raw_maneuvers = lane_raw.get("maneuvers")
    if not isinstance(raw_maneuvers, dict):
        raw_maneuvers = {}

    normalized: dict[str, Any] = {}
    maneuver_order = list(dict.fromkeys([*MANEUVER_ORDER, *list(raw_maneuvers.keys())]))
    for maneuver in maneuver_order:
        raw_config = raw_maneuvers.get(maneuver)
        if not isinstance(raw_config, dict):
            raw_config = {}
        payload = _normalize_maneuver_config_payload(
            maneuver=maneuver,
            raw_config=raw_config,
            frame_width=frame_width,
            frame_height=frame_height,
        )
        if payload:
            normalized[maneuver] = payload
    return normalized or None


def _normalize_direction_rule_payload(
    *,
    lane_raw: dict[str, Any],
    frame_width: int,
    frame_height: int,
) -> Optional[dict[str, Any]]:
    raw_rule = lane_raw.get("direction_rule")
    if not isinstance(raw_rule, dict):
        return None

    raw_direction_path = raw_rule.get("direction_path")
    if not isinstance(raw_direction_path, list) or len(raw_direction_path) < 2:
        raw_direction_path = None
    raw_check_zone = raw_rule.get("check_zone")
    if not isinstance(raw_check_zone, list) or len(raw_check_zone) < 3:
        raw_check_zone = None

    direction_path = normalize_optional_polyline(
        raw_direction_path,
        frame_width,
        frame_height,
    )
    check_zone = normalize_optional_polygon(
        raw_check_zone,
        frame_width,
        frame_height,
    )
    return {
        "enabled": bool(raw_rule.get("enabled", False)),
        "direction_path": direction_path,
        "check_zone": check_zone,
    }


def denormalize_lane_config(lane_config: CameraLaneConfig) -> RuntimeCameraLaneConfig:
    """Đổi toàn bộ polygon của camera từ tọa độ chuẩn hóa sang pixel lúc runtime."""
    frame_width = lane_config.frame_width
    frame_height = lane_config.frame_height

    return RuntimeCameraLaneConfig.model_validate(
        {
            "camera_id": lane_config.camera_id,
            "frame_width": frame_width,
            "frame_height": frame_height,
            "lanes": [
                {
                    "lane_id": lane.lane_id,
                    "polygon": denormalize_polygon(lane.polygon, frame_width, frame_height),
                    "approach_zone": denormalize_optional_polygon(lane.approach_zone, frame_width, frame_height),
                    "commit_gate": denormalize_optional_polygon(lane.commit_gate, frame_width, frame_height),
                    "commit_line": denormalize_optional_polygon(lane.commit_line, frame_width, frame_height),
                    "allowed_maneuvers": lane.allowed_maneuvers,
                    "allowed_lane_changes": lane.allowed_lane_changes,
                    "allowed_vehicle_types": lane.allowed_vehicle_types,
                    "direction_rule": {
                        "enabled": lane.direction_rule.enabled,
                        "direction_path": denormalize_optional_polygon(
                            lane.direction_rule.direction_path,
                            frame_width,
                            frame_height,
                        ),
                        "check_zone": denormalize_optional_polygon(
                            lane.direction_rule.check_zone,
                            frame_width,
                            frame_height,
                        ),
                    }
                    if lane.direction_rule is not None
                    else None,
                    "maneuvers": {
                        maneuver: {
                            "enabled": cfg.enabled,
                            "allowed": cfg.allowed,
                            "turn_zone": denormalize_optional_polygon(
                                cfg.turn_zone,
                                frame_width,
                                frame_height,
                            ),
                            "exit_line": denormalize_optional_polygon(
                                cfg.exit_line,
                                frame_width,
                                frame_height,
                            ),
                            "exit_zone": denormalize_optional_polygon(
                                cfg.exit_zone,
                                frame_width,
                                frame_height,
                            ),
                        }
                        for maneuver, cfg in (lane.maneuvers or {}).items()
                    }
                    or None,
                }
                for lane in lane_config.lanes
            ],
        }
    )


def _normalize_lane_config_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Chuẩn hóa dữ liệu làn từ file cấu hình để thống nhất lưu theo [0, 1]."""
    frame_width = int(raw.get("frame_width") or 1)
    frame_height = int(raw.get("frame_height") or 1)

    normalized_lanes: list[dict[str, Any]] = []
    for lane in raw.get("lanes", []):
        if not isinstance(lane, dict):
            continue
        normalized_lanes.append(
            {
                **lane,
                "polygon": normalize_polygon(lane.get("polygon", []), frame_width, frame_height),
                "approach_zone": normalize_optional_polygon(lane.get("approach_zone"), frame_width, frame_height),
                "commit_gate": normalize_optional_polygon(lane.get("commit_gate"), frame_width, frame_height),
                "commit_line": normalize_optional_polygon(lane.get("commit_line"), frame_width, frame_height),
                "direction_rule": _normalize_direction_rule_payload(
                    lane_raw=lane,
                    frame_width=frame_width,
                    frame_height=frame_height,
                ),
                "maneuvers": _normalize_lane_maneuvers_payload(
                    lane_raw=lane,
                    frame_width=frame_width,
                    frame_height=frame_height,
                ),
            }
        )
    return {
        **raw,
        "lanes": normalized_lanes,
    }


def _compact_lane_config_for_storage(lane_config: CameraLaneConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "camera_id": lane_config.camera_id,
        "frame_width": int(lane_config.frame_width),
        "frame_height": int(lane_config.frame_height),
        "lanes": [],
    }

    for lane in lane_config.lanes:
        lane_payload: dict[str, Any] = {
            "lane_id": int(lane.lane_id),
            "polygon": lane.polygon,
        }
        if lane.approach_zone:
            lane_payload["approach_zone"] = lane.approach_zone
        if lane.commit_gate:
            lane_payload["commit_gate"] = lane.commit_gate
        if lane.commit_line:
            lane_payload["commit_line"] = lane.commit_line
        if lane.allowed_lane_changes is not None:
            lane_payload["allowed_lane_changes"] = lane.allowed_lane_changes
        if lane.allowed_vehicle_types is not None:
            lane_payload["allowed_vehicle_types"] = lane.allowed_vehicle_types
        if lane.direction_rule is not None:
            direction_cfg = lane.direction_rule
            compact_direction_cfg: dict[str, Any] = {"enabled": bool(direction_cfg.enabled)}
            if direction_cfg.direction_path:
                compact_direction_cfg["direction_path"] = direction_cfg.direction_path
            if direction_cfg.check_zone:
                compact_direction_cfg["check_zone"] = direction_cfg.check_zone
            if compact_direction_cfg != {"enabled": False}:
                lane_payload["direction_rule"] = compact_direction_cfg

        maneuver_payloads: dict[str, Any] = {}
        for maneuver in MANEUVER_ORDER:
            cfg = (lane.maneuvers or {}).get(maneuver)
            if cfg is None:
                continue

            compact: dict[str, Any] = {
                "enabled": bool(cfg.enabled),
                "allowed": bool(cfg.allowed),
            }
            if cfg.turn_zone:
                compact["turn_zone"] = cfg.turn_zone
            if cfg.exit_line:
                compact["exit_line"] = cfg.exit_line
            if cfg.exit_zone:
                compact["exit_zone"] = cfg.exit_zone

            is_default_disallowed = compact == {"enabled": True, "allowed": False}
            if not is_default_disallowed:
                maneuver_payloads[maneuver] = compact

        if maneuver_payloads:
            lane_payload["maneuvers"] = maneuver_payloads

        payload["lanes"].append(lane_payload)

    return payload


def _setting(settings: dict[str, Any], path: tuple[str, ...], default: Any) -> Any:
    current: Any = settings
    for key in path:
        if not isinstance(current, dict):
            return default
        if key not in current:
            return default
        current = current[key]
    return default if current is None else current


def load_app_config(repo_root: Path) -> AppConfig:
    """Tải cấu hình ứng dụng từ `settings.json` với schema theo nhóm chức năng."""
    config_dir = repo_root / "config"
    settings_path = config_dir / "settings.json"
    cameras_path = config_dir / "cameras.json"
    lane_configs_dir = config_dir / "lane_configs"
    background_images_dir = config_dir / "background_images"
    evidence_images_dir = config_dir / "evidence_images"

    default_db_path = config_dir / "traffic_warning.sqlite"
    settings: dict[str, Any] = _read_json(settings_path) if settings_path.exists() else {}

    db_path_raw = _setting(settings, ("database", "path"), str(default_db_path))
    db_path = Path(str(db_path_raw))
    if not db_path.is_absolute():
        db_path = repo_root / db_path

    return AppConfig(
        settings_path=settings_path,
        config_dir=config_dir,
        cameras_path=cameras_path,
        lane_configs_dir=lane_configs_dir,
        background_images_dir=background_images_dir,
        evidence_images_dir=evidence_images_dir,
        db_path=db_path,
        detector_weights_path=str(_setting(settings, ("detection", "weights_path"), "backend/yolov8n.pt")),
        detector_device=str(_setting(settings, ("detection", "device"), "auto")),
        detector_conf_threshold=float(_setting(settings, ("detection", "confidence_threshold"), 0.28)),
        detector_iou_threshold=float(_setting(settings, ("detection", "iou_threshold"), 0.7)),
        detector_allowed_classes=_setting(
            settings,
            ("detection", "allowed_classes"),
            list(DEFAULT_DETECTOR_ALLOWED_CLASSES),
        ),
        tracker_config=str(_setting(settings, ("tracking", "tracker_config"), "bytetrack.yaml")),
        vehicle_type_history_window_ms=int(_setting(settings, ("tracking", "vehicle_type_history", "window_ms"), 4000)),
        vehicle_type_history_size=int(_setting(settings, ("tracking", "vehicle_type_history", "size"), 12)),
        stable_track_max_idle_ms=int(_setting(settings, ("tracking", "stable_track", "max_idle_ms"), 1500)),
        stable_track_min_iou_for_rebind=float(
            _setting(settings, ("tracking", "stable_track", "min_iou_for_rebind"), 0.15)
        ),
        stable_track_max_normalized_distance=float(
            _setting(settings, ("tracking", "stable_track", "max_normalized_distance"), 1.6)
        ),
        temporal_lane_observation_window_ms=int(
            _setting(settings, ("lane_assignment", "temporal", "observation_window_ms"), 1200)
        ),
        temporal_lane_min_majority_hits=int(
            _setting(settings, ("lane_assignment", "temporal", "min_majority_hits"), 3)
        ),
        temporal_lane_switch_min_duration_ms=int(
            _setting(settings, ("lane_assignment", "temporal", "switch_min_duration_ms"), 700)
        ),
        lane_assignment_overlap=LaneAssignmentOverlapConfig.model_validate(
            _setting(settings, ("lane_assignment", "overlap_preference"), {}) or {}
        ),
        vehicle_type_history_recency_weight_bias=float(
            _setting(settings, ("tracking", "vehicle_type_history", "recency_weight_bias"), 0.15)
        ),
        track_push_interval_ms=int(_setting(settings, ("websocket", "track_push_interval_ms"), 200)),
        websocket_listener_queue_maxsize=int(
            _setting(settings, ("websocket", "listener_queue_maxsize"), 200)
        ),
        wrong_lane_min_duration_ms=int(_setting(settings, ("wrong_lane", "min_duration_ms"), 1200)),
        turn_region_min_hits=int(_setting(settings, ("turn_detection", "turn_region_min_hits"), 3)),
        turn_state_timeout_ms=int(_setting(settings, ("turn_detection", "turn_state_timeout_ms"), 3000)),
        trajectory_history_window_ms=int(_setting(settings, ("turn_detection", "trajectory_history_window_ms"), 2000)),
        turn_detection_heading=TurnDetectionHeadingConfig.model_validate(
            _setting(settings, ("turn_detection", "heading"), {}) or {}
        ),
        turn_detection_curvature=TurnDetectionCurvatureConfig.model_validate(
            _setting(settings, ("turn_detection", "curvature"), {}) or {}
        ),
        turn_detection_opposite_direction=TurnDetectionOppositeDirectionConfig.model_validate(
            _setting(settings, ("turn_detection", "opposite_direction"), {}) or {}
        ),
        turn_detection_trajectory=TurnDetectionTrajectoryConfig.model_validate(
            _setting(settings, ("turn_detection", "trajectory"), {}) or {}
        ),
        direction_detection_defaults=DirectionDetectionDefaultsConfig.model_validate(
            _setting(settings, ("direction_detection", "defaults"), {}) or {}
        ),
        line_crossing_side_tolerance_px=float(
            _setting(settings, ("evidence_fusion", "line_crossing", "side_tolerance_px"), 2.0)
        ),
        line_crossing_min_pre_frames=int(
            _setting(settings, ("evidence_fusion", "line_crossing", "min_pre_frames"), 2)
        ),
        line_crossing_min_post_frames=int(
            _setting(settings, ("evidence_fusion", "line_crossing", "min_post_frames"), 2)
        ),
        line_crossing_min_displacement_px=float(
            _setting(settings, ("evidence_fusion", "line_crossing", "min_displacement_px"), 2.0)
        ),
        line_crossing_min_displacement_ratio=float(
            _setting(settings, ("evidence_fusion", "line_crossing", "min_displacement_ratio"), 0.02)
        ),
        line_crossing_max_gap_ms=int(
            _setting(settings, ("evidence_fusion", "line_crossing", "max_gap_ms"), 400)
        ),
        line_crossing_cooldown_ms=int(
            _setting(settings, ("evidence_fusion", "line_crossing", "cooldown_ms"), 1200)
        ),
        violation_rearm_window_ms=int(
            _setting(settings, ("event_lifecycle", "violation_rearm_window_ms"), 3500)
        ),
        evidence_expire_ms=int(
            _setting(settings, ("evidence_fusion", "evidence_expire_ms"), 1600)
        ),
        motion_window_samples=int(
            _setting(settings, ("evidence_fusion", "motion_window_samples"), 8)
        ),
        evidence_fusion_turn_scoring=EvidenceFusionTurnScoringConfig.model_validate(
            _setting(settings, ("evidence_fusion", "turn_scoring"), {}) or {}
        ),
        state_prune_max_age_s=float(_setting(settings, ("event_lifecycle", "state_prune_max_age_s"), 60.0)),
        rtsp_reconnect_delay_s=float(_setting(settings, ("camera", "stream", "rtsp_reconnect_delay_s"), 2.0)),
        preview_max_fps=float(_setting(settings, ("performance", "preview", "max_fps"), 15.0)),
        preview_jpeg_quality=int(_setting(settings, ("performance", "preview", "jpeg_quality"), 75)),
        processing_fps_window_s=float(_setting(settings, ("performance", "processing", "fps_window_s"), 1.5)),
        evidence_crop_expand_x_ratio=float(_setting(settings, ("geometry", "evidence_crop", "expand_x_ratio"), 0.28)),
        evidence_crop_expand_y_top_ratio=float(
            _setting(settings, ("geometry", "evidence_crop", "expand_y_top_ratio"), 0.32)
        ),
        evidence_crop_expand_y_bottom_ratio=float(
            _setting(settings, ("geometry", "evidence_crop", "expand_y_bottom_ratio"), 0.27)
        ),
        evidence_crop_min_size_px=int(_setting(settings, ("geometry", "evidence_crop", "min_size_px"), 24)),
        evidence_jpeg_quality=int(_setting(settings, ("geometry", "evidence_image", "jpeg_quality"), 92)),
        license_plate=LicensePlateConfig.model_validate(
            _setting(settings, ("license_plate",), {}) or {}
        ),
        analytics_chart=AnalyticsChartConfig.model_validate(
            _setting(settings, ("analytics", "chart"), {}) or {}
        ),
        ui=UiConfig.model_validate(_setting(settings, ("ui",), {}) or {}),
    )


def load_cameras(repo_root: Path) -> list[CameraConfig]:
    cfg = load_app_config(repo_root)
    raw = _read_json(cfg.cameras_path)
    cameras: list[CameraConfig] = []
    for cam in raw.get("cameras", []):
        cameras.append(CameraConfig.model_validate(cam))
    return cameras


def save_cameras(repo_root: Path, cameras: list[CameraConfig]) -> None:
    cfg = load_app_config(repo_root)
    payload = {"cameras": [cam.model_dump(mode="json", exclude_none=True) for cam in cameras]}
    _write_json(cfg.cameras_path, payload)


def load_lane_config_for_camera(repo_root: Path, camera_id: str) -> CameraLaneConfig:
    cfg = load_app_config(repo_root)
    path = cfg.lane_configs_dir / f"{camera_id}.json"
    raw = _read_json(path)
    return CameraLaneConfig.model_validate(_normalize_lane_config_payload(raw))


def save_lane_config_for_camera(repo_root: Path, lane_config: CameraLaneConfig) -> None:
    cfg = load_app_config(repo_root)
    path = cfg.lane_configs_dir / f"{lane_config.camera_id}.json"
    _write_json(path, _compact_lane_config_for_storage(lane_config))


def delete_lane_config_for_camera(repo_root: Path, camera_id: str) -> None:
    cfg = load_app_config(repo_root)
    path = cfg.lane_configs_dir / f"{camera_id}.json"
    if path.exists():
        path.unlink()


def validate_no_shared_lanes_across_cameras(repo_root: Path) -> None:
    """Kiểm tra camera và lane config không bị trùng hoặc lệch danh sách làn."""
    cameras = load_cameras(repo_root)
    seen_camera_ids: set[str] = set()
    for cam in cameras:
        if cam.camera_id in seen_camera_ids:
            raise ValueError(f"Duplicate camera_id detected: {cam.camera_id}")
        seen_camera_ids.add(cam.camera_id)
        lane_cfg = load_lane_config_for_camera(repo_root, cam.camera_id)
        lane_ids = {lp.lane_id for lp in lane_cfg.lanes}
        if lane_cfg.camera_id != cam.camera_id:
            raise ValueError(
                f"Lane config camera_id mismatch: expected {cam.camera_id}, got {lane_cfg.camera_id}"
            )
        if set(cam.monitored_lanes) != lane_ids:
            raise ValueError(
                f"Camera {cam.camera_id} monitored_lanes mismatch with lane config: "
                f"camera.monitored_lanes={sorted(cam.monitored_lanes)} vs lane_config.lanes={sorted(lane_ids)}"
            )
        if len(lane_ids) != len(lane_cfg.lanes):
            raise ValueError(f"Camera {cam.camera_id} contains duplicate lane_id values in its lane config")

