from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import load_app_config, load_lane_config_for_camera, save_lane_config_for_camera


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
            "allowed_classes": ["motorcycle", "car"],
        },
        "tracking": {
            "tracker_config": "bytetrack.yaml",
            "vehicle_type_history": {"window_ms": 4200, "size": 10, "recency_weight_bias": 0.2},
            "stable_track": {
                "max_idle_ms": 1300,
                "min_iou_for_rebind": 0.17,
                "max_normalized_distance": 1.4,
            },
        },
        "lane_assignment": {
            "temporal": {"observation_window_ms": 900, "min_majority_hits": 2, "switch_min_duration_ms": 600},
            "overlap_preference": {"preferred_lane_overlap_ratio": 0.85, "preferred_lane_overlap_margin_px": 5.0},
        },
        "websocket": {"track_push_interval_ms": 150, "listener_queue_maxsize": 120},
        "wrong_lane": {"min_duration_ms": 1000},
        "direction_detection": {
            "defaults": {
                "same_direction_cos_threshold": 0.24,
                "opposite_direction_cos_threshold": -0.28,
                "min_duration_ms": 550,
                "min_displacement_px": 7.5,
                "min_samples": 3,
            }
        },
        "turn_detection": {
            "turn_region_min_hits": 2,
            "turn_state_timeout_ms": 2800,
            "trajectory_history_window_ms": 1800,
            "heading": {"straight_max_deg": 30.0},
            "curvature": {"u_turn_min": 0.22},
            "opposite_direction": {"cos_threshold": -0.25},
            "trajectory": {"sample_inside_polygon_min_hits": 3},
        },
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
            "turn_scoring": {"threshold_u_turn_with_exit": 5.4},
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
        "ui": {
            "monitoring": {
                "trajectory": {"default_limit": 40, "max_points_per_vehicle": 55},
                "violation": {"list_max_rows": 120},
                "processing_fps": {"stale_after_ms": 1200},
            }
        },
        "license_plate": {
            "enabled": True,
            "detector_weights_path": "backend/license_plate_yolov8.pt",
            "detector_confidence_threshold": 0.4,
            "ocr_backend": "paddleocr",
            "easyocr_lang": "en",
            "easyocr_use_gpu": False,
            "paddle_ocr_version": "PP-OCRv5",
            "paddle_text_detection_model_name": "PP-OCRv5_mobile_det",
            "paddle_text_recognition_model_name": "PP-OCRv5_mobile_rec",
            "paddle_lang": "en",
            "paddle_use_gpu": False,
            "read_interval_ms": 650,
            "min_ocr_confidence": 0.7,
            "consensus_min_hits": 3,
            "candidate_window_ms": 5000,
            "max_attempts_before_unreadable": 7,
            "crop_expand_x_ratio": 0.12,
            "crop_expand_y_ratio": 0.1,
            "image_jpeg_quality": 90,
        },
    }
    (config_dir / "settings.json").write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")

    cfg = load_app_config(repo_root)

    assert cfg.db_path == repo_root / "config" / "test.sqlite"
    assert cfg.detector_device == "cpu"
    assert cfg.detector_conf_threshold == 0.22
    assert cfg.detector_allowed_classes == ["motorcycle", "car"]
    assert cfg.track_push_interval_ms == 150
    assert cfg.websocket_listener_queue_maxsize == 120
    assert cfg.line_crossing_max_gap_ms == 350
    assert cfg.vehicle_type_history_recency_weight_bias == 0.2
    assert cfg.lane_assignment_overlap.preferred_lane_overlap_ratio == 0.85
    assert cfg.rtsp_reconnect_delay_s == 1.25
    assert cfg.preview_max_fps == 12.0
    assert cfg.processing_fps_window_s == 2.0
    assert cfg.turn_detection_heading.straight_max_deg == 30.0
    assert cfg.turn_detection_curvature.u_turn_min == 0.22
    assert cfg.turn_detection_opposite_direction.cos_threshold == -0.25
    assert cfg.turn_detection_trajectory.sample_inside_polygon_min_hits == 3
    assert cfg.direction_detection_defaults.same_direction_cos_threshold == 0.24
    assert cfg.direction_detection_defaults.opposite_direction_cos_threshold == -0.28
    assert cfg.direction_detection_defaults.min_duration_ms == 550
    assert cfg.direction_detection_defaults.min_displacement_px == 7.5
    assert cfg.direction_detection_defaults.min_samples == 3
    assert cfg.evidence_fusion_turn_scoring.threshold_u_turn_with_exit == 5.4
    assert cfg.ui.monitoring.trajectory.default_limit == 40
    assert cfg.ui.monitoring.violation.list_max_rows == 120
    assert cfg.ui.monitoring.processing_fps.stale_after_ms == 1200
    assert cfg.evidence_jpeg_quality == 88
    assert cfg.license_plate.enabled is True
    assert cfg.license_plate.detector_confidence_threshold == 0.4
    assert cfg.license_plate.ocr_backend == "paddleocr"
    assert cfg.license_plate.easyocr_lang == "en"
    assert cfg.license_plate.easyocr_use_gpu is False
    assert cfg.license_plate.paddle_ocr_version == "PP-OCRv5"
    assert cfg.license_plate.paddle_text_detection_model_name == "PP-OCRv5_mobile_det"
    assert cfg.license_plate.paddle_text_recognition_model_name == "PP-OCRv5_mobile_rec"
    assert cfg.license_plate.paddle_lang == "en"
    assert cfg.license_plate.paddle_use_gpu is False
    assert cfg.license_plate.read_interval_ms == 650
    assert cfg.license_plate.consensus_min_hits == 3


def test_lane_config_keeps_only_direction_geometry_and_strips_legacy_thresholds(tmp_path: Path) -> None:
    repo_root = tmp_path
    config_dir = repo_root / "config"
    lane_configs_dir = config_dir / "lane_configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    lane_configs_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "cameras.json").write_text('{"cameras": []}', encoding="utf-8")
    (config_dir / "settings.json").write_text(
        json.dumps(
            {
                "direction_detection": {
                    "defaults": {
                        "same_direction_cos_threshold": 0.21,
                        "opposite_direction_cos_threshold": -0.31,
                        "min_duration_ms": 500,
                        "min_displacement_px": 6.5,
                        "min_samples": 3,
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (lane_configs_dir / "cam_settings.json").write_text(
        json.dumps(
            {
                "camera_id": "cam_settings",
                "frame_width": 100,
                "frame_height": 100,
                "lanes": [
                    {
                        "lane_id": 1,
                        "polygon": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
                        "direction_rule": {
                            "enabled": True,
                            "direction_path": [[0.5, 0.9], [0.5, 0.1]],
                            "check_zone": [],
                            "same_direction_cos_threshold": 0.99,
                            "opposite_direction_cos_threshold": -0.99,
                            "min_duration_ms": 9999,
                            "min_displacement_px": 99,
                            "min_samples": 9,
                        },
                    },
                    {
                        "lane_id": 2,
                        "polygon": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
                        "direction_rule": {
                            "enabled": True,
                            "direction_path": [[0.2, 0.9], [0.2, 0.1]],
                            "opposite_direction_cos_threshold": -0.45,
                            "min_duration_ms": 900,
                        },
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    lane_config = load_lane_config_for_camera(repo_root, "cam_settings")
    lane_1 = lane_config.lanes[0].direction_rule
    lane_2 = lane_config.lanes[1].direction_rule

    assert lane_1 is not None
    assert lane_1.check_zone is None
    assert not hasattr(lane_1, "same_direction_cos_threshold")
    assert not hasattr(lane_1, "opposite_direction_cos_threshold")
    assert not hasattr(lane_1, "min_duration_ms")
    assert not hasattr(lane_1, "min_displacement_px")
    assert not hasattr(lane_1, "min_samples")

    assert lane_2 is not None
    assert not hasattr(lane_2, "opposite_direction_cos_threshold")
    assert not hasattr(lane_2, "min_duration_ms")

    save_lane_config_for_camera(repo_root, lane_config)
    saved_payload = json.loads((lane_configs_dir / "cam_settings.json").read_text(encoding="utf-8"))
    for saved_lane in saved_payload["lanes"]:
        saved_rule = saved_lane["direction_rule"]
        assert set(saved_rule) == {"enabled", "direction_path"}
