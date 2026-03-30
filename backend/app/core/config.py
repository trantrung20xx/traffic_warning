from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from app.schemas.camera import CameraConfig


class LanePolygon(BaseModel):
    lane_id: int
    # Polygon points in pixel coordinates for this camera frame
    polygon: list[list[float]]  # [[x,y], ...]

    # Geometry-based maneuver classification:
    # Define where the vehicle "arrives" in order to infer turn direction.
    # Keys are maneuver names: e.g. "straight", "left", "right", "u_turn".
    turn_regions: Optional[dict[str, list[list[float]]]] = None

    # If a vehicle's primary lane is this lane, only these maneuvers are allowed.
    allowed_maneuvers: Optional[list[str]] = None

    # Lane-change policy for "Đi sai làn":
    # If a vehicle enters a lane not in allowed_lane_changes, we consider it illegal.
    # By default skeleton allows only staying in its primary lane.
    allowed_lane_changes: Optional[list[int]] = None


class CameraLaneConfig(BaseModel):
    camera_id: str
    frame_width: int
    frame_height: int
    lanes: list[LanePolygon]


class AppConfig(BaseModel):
    settings_path: Path
    config_dir: Path
    cameras_path: Path
    lane_configs_dir: Path
    db_path: Path

    # Detector / performance settings
    detector_conf_threshold: float = 0.35
    detector_iou_threshold: float = 0.7
    resize_frame: bool = True

    # Realtime streaming / logic thresholds
    track_push_interval_ms: int = 200
    wrong_lane_min_duration_ms: int = 1200
    turn_region_min_hits: int = 3


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_app_config(repo_root: Path) -> AppConfig:
    config_dir = repo_root / "config"
    settings_path = config_dir / "settings.json"
    cameras_path = config_dir / "cameras.json"
    lane_configs_dir = config_dir / "lane_configs"

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
            db_path=db_path,
            detector_conf_threshold=float(settings.get("detector_conf_threshold", 0.35)),
            detector_iou_threshold=float(settings.get("detector_iou_threshold", 0.7)),
            resize_frame=bool(settings.get("resize_frame", True)),
            track_push_interval_ms=int(settings.get("track_push_interval_ms", 200)),
            wrong_lane_min_duration_ms=int(
                settings.get(
                    "wrong_lane_min_duration_ms",
                    # backward compatible key (older skeleton)
                    settings.get("wrong_lane_min_consecutive_frames", 1200),
                )
            ),
            turn_region_min_hits=int(settings.get("turn_region_min_hits", 3)),
        )

    return AppConfig(
        settings_path=settings_path,
        config_dir=config_dir,
        cameras_path=cameras_path,
        lane_configs_dir=lane_configs_dir,
        db_path=db_path,
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
    return CameraLaneConfig.model_validate(raw)


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

