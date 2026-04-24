from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import load_app_config


def test_load_app_config_reads_grouped_settings_schema(tmp_path: Path) -> None:
    repo_root = tmp_path
    config_dir = repo_root / "config"
    lane_configs_dir = config_dir / "lane_configs"
    background_images_dir = config_dir / "background_images"
    evidence_images_dir = config_dir / "evidence_images"
    config_dir.mkdir(parents=True, exist_ok=True)
    lane_configs_dir.mkdir(parents=True, exist_ok=True)
    background_images_dir.mkdir(parents=True, exist_ok=True)
    evidence_images_dir.mkdir(parents=True, exist_ok=True)

    (config_dir / "cameras.json").write_text('{"cameras": []}', encoding="utf-8")
    settings = {
        "database": {"path": "config/test.sqlite"},
        "camera": {"stream": {"rtsp_reconnect_delay_s": 1.25}},
        "detection": {
            "weights_path": "backend/yolov8n.pt",
            "device": "cpu",
            "confidence_threshold": 0.22,
            "iou_threshold": 0.61,
        },
        "tracking": {
            "tracker_config": "bytetrack.yaml",
            "vehicle_type_history": {"window_ms": 4200, "size": 10},
            "stable_track": {
                "max_idle_ms": 1300,
                "min_iou_for_rebind": 0.17,
                "max_normalized_distance": 1.4,
            },
        },
        "lane_assignment": {"temporal": {"observation_window_ms": 900, "min_majority_hits": 2, "switch_min_duration_ms": 600}},
        "websocket": {"track_push_interval_ms": 150},
        "wrong_lane": {"min_duration_ms": 1000},
        "turn_detection": {"turn_region_min_hits": 2, "turn_state_timeout_ms": 2800, "trajectory_history_window_ms": 1800},
        "evidence_fusion": {
            "line_crossing": {
                "side_tolerance_px": 1.5,
                "min_pre_frames": 2,
                "min_post_frames": 2,
                "min_displacement_px": 1.8,
                "min_displacement_ratio": 0.03,
                "max_gap_ms": 350,
                "cooldown_ms": 900,
            },
            "evidence_expire_ms": 1400,
            "motion_window_samples": 7,
        },
        "event_lifecycle": {"violation_rearm_window_ms": 2600, "state_prune_max_age_s": 45.0},
        "performance": {
            "preview": {"max_fps": 12.0, "jpeg_quality": 70},
            "processing": {"fps_window_s": 2.0},
        },
        "geometry": {
            "evidence_crop": {
                "expand_x_ratio": 0.3,
                "expand_y_top_ratio": 0.35,
                "expand_y_bottom_ratio": 0.25,
                "min_size_px": 22,
            },
            "evidence_image": {"jpeg_quality": 88},
        },
    }
    (config_dir / "settings.json").write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")

    cfg = load_app_config(repo_root)

    assert cfg.db_path == repo_root / "config" / "test.sqlite"
    assert cfg.detector_device == "cpu"
    assert cfg.detector_conf_threshold == 0.22
    assert cfg.track_push_interval_ms == 150
    assert cfg.line_crossing_max_gap_ms == 350
    assert cfg.rtsp_reconnect_delay_s == 1.25
    assert cfg.preview_max_fps == 12.0
    assert cfg.processing_fps_window_s == 2.0
    assert cfg.evidence_jpeg_quality == 88
