from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, field_validator

from app.schemas.camera import CameraConfig

ALLOWED_VEHICLE_TYPES = {"motorcycle", "car", "truck", "bus"}
ALLOWED_MANEUVERS = {"straight", "left", "right", "u_turn"}


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


def _validate_maneuver_zones(
    value: Optional[dict[str, list[list[float]]]],
    *,
    field_name: str,
) -> Optional[dict[str, list[list[float]]]]:
    if value is None:
        return value
    for maneuver, polygon in value.items():
        if maneuver not in ALLOWED_MANEUVERS:
            raise ValueError(f"unsupported maneuver key: {maneuver}")
        _validate_polygon_points(polygon, field_name=f"{field_name} '{maneuver}'")
    return value


def _validate_maneuver_lines(
    value: Optional[dict[str, list[list[float]]]],
    *,
    field_name: str,
) -> Optional[dict[str, list[list[float]]]]:
    if value is None:
        return value
    for maneuver, line in value.items():
        if maneuver not in ALLOWED_MANEUVERS:
            raise ValueError(f"unsupported maneuver key: {maneuver}")
        _validate_line_points(line, field_name=f"{field_name} '{maneuver}'")
    return value


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


class CameraLaneConfig(BaseModel):
    camera_id: str
    frame_width: int
    frame_height: int
    lanes: list[LanePolygon]
    turn_corridors: Optional[dict[str, list[list[float]]]] = None
    exit_zones: Optional[dict[str, list[list[float]]]] = None
    exit_lines: Optional[dict[str, list[list[float]]]] = None

    @field_validator("turn_corridors", "exit_zones")
    @classmethod
    def validate_geometry_collection(
        cls,
        value: Optional[dict[str, list[list[float]]]],
        info,
    ) -> Optional[dict[str, list[list[float]]]]:
        return _validate_maneuver_zones(value, field_name=info.field_name)

    @field_validator("exit_lines")
    @classmethod
    def validate_line_collection(
        cls,
        value: Optional[dict[str, list[list[float]]]],
        info,
    ) -> Optional[dict[str, list[list[float]]]]:
        return _validate_maneuver_lines(value, field_name=info.field_name)


class RuntimeLanePolygon(BaseModel):
    lane_id: int
    polygon: list[list[float]]
    approach_zone: Optional[list[list[float]]] = None
    commit_gate: Optional[list[list[float]]] = None
    commit_line: Optional[list[list[float]]] = None
    allowed_maneuvers: Optional[list[str]] = None
    allowed_lane_changes: Optional[list[int]] = None
    allowed_vehicle_types: Optional[list[str]] = None


class RuntimeCameraLaneConfig(BaseModel):
    camera_id: str
    frame_width: int
    frame_height: int
    lanes: list[RuntimeLanePolygon]
    turn_corridors: Optional[dict[str, list[list[float]]]] = None
    exit_zones: Optional[dict[str, list[list[float]]]] = None
    exit_lines: Optional[dict[str, list[list[float]]]] = None


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
    tracker_config: str = "bytetrack.yaml"
    vehicle_type_history_window_ms: int = 4000
    vehicle_type_history_size: int = 12
    stable_track_max_idle_ms: int = 1500
    stable_track_min_iou_for_rebind: float = 0.15
    stable_track_max_normalized_distance: float = 1.6
    temporal_lane_observation_window_ms: int = 1200
    temporal_lane_min_majority_hits: int = 3
    temporal_lane_switch_min_duration_ms: int = 700
    resize_frame: bool = True

    # Ngưỡng cho luồng realtime, phát hiện vi phạm và ảnh bằng chứng.
    track_push_interval_ms: int = 200
    wrong_lane_min_duration_ms: int = 1200
    turn_region_min_hits: int = 3
    turn_candidate_window_ms: int = 500
    turn_state_timeout_ms: int = 3000
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
    analytics_chart: AnalyticsChartConfig = AnalyticsChartConfig()


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


def normalize_geometry_collection(
    value: Optional[dict[str, Any]],
    frame_width: int,
    frame_height: int,
) -> Optional[dict[str, list[list[float]]]]:
    if value is None:
        return None
    return {
        maneuver: normalize_polygon(polygon, frame_width, frame_height)
        for maneuver, polygon in value.items()
    }


def denormalize_geometry_collection(
    value: Optional[dict[str, list[list[float]]]],
    frame_width: int,
    frame_height: int,
) -> Optional[dict[str, list[list[float]]]]:
    if value is None:
        return None
    return {
        maneuver: denormalize_polygon(polygon, frame_width, frame_height)
        for maneuver, polygon in value.items()
    }


def normalize_line_collection(
    value: Optional[dict[str, Any]],
    frame_width: int,
    frame_height: int,
) -> Optional[dict[str, list[list[float]]]]:
    if value is None:
        return None
    return {
        maneuver: normalize_polygon(line, frame_width, frame_height)
        for maneuver, line in value.items()
    }


def denormalize_line_collection(
    value: Optional[dict[str, list[list[float]]]],
    frame_width: int,
    frame_height: int,
) -> Optional[dict[str, list[list[float]]]]:
    if value is None:
        return None
    return {
        maneuver: denormalize_polygon(line, frame_width, frame_height)
        for maneuver, line in value.items()
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
                }
                for lane in lane_config.lanes
            ],
            "turn_corridors": denormalize_geometry_collection(lane_config.turn_corridors, frame_width, frame_height),
            "exit_zones": denormalize_geometry_collection(lane_config.exit_zones, frame_width, frame_height),
            "exit_lines": denormalize_line_collection(lane_config.exit_lines, frame_width, frame_height),
        }
    )


def _normalize_lane_config_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Chuẩn hóa dữ liệu làn từ file cấu hình để thống nhất lưu theo [0, 1]."""
    frame_width = int(raw.get("frame_width") or 1)
    frame_height = int(raw.get("frame_height") or 1)
    return {
        **raw,
        "turn_corridors": normalize_geometry_collection(raw.get("turn_corridors"), frame_width, frame_height),
        "exit_zones": normalize_geometry_collection(raw.get("exit_zones"), frame_width, frame_height),
        "exit_lines": normalize_line_collection(raw.get("exit_lines"), frame_width, frame_height),
        "lanes": [
            {
                **lane,
                "polygon": normalize_polygon(lane.get("polygon", []), frame_width, frame_height),
                "approach_zone": normalize_optional_polygon(lane.get("approach_zone"), frame_width, frame_height),
                "commit_gate": normalize_optional_polygon(lane.get("commit_gate"), frame_width, frame_height),
                "commit_line": normalize_optional_polygon(lane.get("commit_line"), frame_width, frame_height),
            }
            for lane in raw.get("lanes", [])
        ],
    }


def load_app_config(repo_root: Path) -> AppConfig:
    """Tải cấu hình ứng dụng từ `settings.json` và áp dụng giá trị mặc định khi thiếu."""
    config_dir = repo_root / "config"
    settings_path = config_dir / "settings.json"
    cameras_path = config_dir / "cameras.json"
    lane_configs_dir = config_dir / "lane_configs"
    background_images_dir = config_dir / "background_images"
    evidence_images_dir = config_dir / "evidence_images"

    db_path = config_dir / "traffic_warning.sqlite"

    if settings_path.exists():
        settings = _read_json(settings_path)
        db_path_raw = settings.get("db_path", str(db_path))
        db_path = Path(db_path_raw)
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
            detector_weights_path=str(settings.get("detector_weights_path", "backend/yolov8n.pt")),
            detector_device=str(settings.get("detector_device", "auto")),
            detector_conf_threshold=float(settings.get("detector_conf_threshold", 0.28)),
            detector_iou_threshold=float(settings.get("detector_iou_threshold", 0.7)),
            tracker_config=str(settings.get("tracker_config", "bytetrack.yaml")),
            vehicle_type_history_window_ms=int(settings.get("vehicle_type_history_window_ms", 4000)),
            vehicle_type_history_size=int(settings.get("vehicle_type_history_size", 12)),
            stable_track_max_idle_ms=int(settings.get("stable_track_max_idle_ms", 1500)),
            stable_track_min_iou_for_rebind=float(settings.get("stable_track_min_iou_for_rebind", 0.15)),
            stable_track_max_normalized_distance=float(settings.get("stable_track_max_normalized_distance", 1.6)),
            temporal_lane_observation_window_ms=int(settings.get("temporal_lane_observation_window_ms", 1200)),
            temporal_lane_min_majority_hits=int(settings.get("temporal_lane_min_majority_hits", 3)),
            temporal_lane_switch_min_duration_ms=int(settings.get("temporal_lane_switch_min_duration_ms", 700)),
            resize_frame=bool(settings.get("resize_frame", True)),
            track_push_interval_ms=int(settings.get("track_push_interval_ms", 200)),
            wrong_lane_min_duration_ms=int(
                settings.get(
                    "wrong_lane_min_duration_ms",
                    # Giữ tương thích với khóa cũ trong các bộ cấu hình trước đây.
                    settings.get("wrong_lane_min_consecutive_frames", 1200),
                )
            ),
            turn_region_min_hits=int(settings.get("turn_region_min_hits", 3)),
            turn_candidate_window_ms=int(settings.get("turn_candidate_window_ms", 500)),
            turn_state_timeout_ms=int(settings.get("turn_state_timeout_ms", 3000)),
            state_prune_max_age_s=float(settings.get("state_prune_max_age_s", 60.0)),
            rtsp_reconnect_delay_s=float(settings.get("rtsp_reconnect_delay_s", 2.0)),
            preview_max_fps=float(settings.get("preview_max_fps", 15.0)),
            preview_jpeg_quality=int(settings.get("preview_jpeg_quality", 75)),
            processing_fps_window_s=float(settings.get("processing_fps_window_s", 1.5)),
            evidence_crop_expand_x_ratio=float(settings.get("evidence_crop_expand_x_ratio", 0.28)),
            evidence_crop_expand_y_top_ratio=float(settings.get("evidence_crop_expand_y_top_ratio", 0.32)),
            evidence_crop_expand_y_bottom_ratio=float(settings.get("evidence_crop_expand_y_bottom_ratio", 0.27)),
            evidence_crop_min_size_px=int(settings.get("evidence_crop_min_size_px", 24)),
            evidence_jpeg_quality=int(settings.get("evidence_jpeg_quality", 92)),
            analytics_chart=AnalyticsChartConfig.model_validate(settings.get("analytics_chart") or {}),
        )

    return AppConfig(
        settings_path=settings_path,
        config_dir=config_dir,
        cameras_path=cameras_path,
        lane_configs_dir=lane_configs_dir,
        background_images_dir=background_images_dir,
        evidence_images_dir=evidence_images_dir,
        db_path=db_path,
        detector_weights_path="backend/yolov8n.pt",
        detector_device="auto",
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
    _write_json(path, lane_config.model_dump(mode="json", exclude_none=True))


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

