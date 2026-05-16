from __future__ import annotations

from pathlib import Path

import json
import pytest

from traffic_camera_node.config import (
    load_config,
    next_image_tuning_profile,
    persist_image_tuning_profile,
)


def test_minimal_config_loads_with_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "settings.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "camera": {"width": 1920, "height": 1080, "fps": 25},
                "image_tuning": {"profile": "normal"},
                "gpio": {"enabled": True},
            }
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path)
    assert cfg.camera.width == 1920
    assert cfg.camera.height == 1080
    assert cfg.camera.fps == 25
    assert cfg.identity.port_range_start == 8554
    assert cfg.gpio.leds.online == 17
    assert cfg.stream.pipeline_mode == "auto"
    assert cfg.stream.source == "auto"
    assert cfg.stream.usb_device == "auto"
    assert cfg.stream.usb_input_format == "auto"


def test_invalid_profile_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "settings.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "camera": {"width": 1920, "height": 1080, "fps": 25},
                "image_tuning": {"profile": "impossible_profile"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(config_path)


def test_invalid_stream_pipeline_mode_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "settings.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "camera": {"width": 1920, "height": 1080, "fps": 25},
                "image_tuning": {"profile": "normal"},
                "stream": {"pipeline_mode": "broken_mode"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(config_path)


def test_invalid_stream_source_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "settings.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "camera": {"width": 1920, "height": 1080, "fps": 25},
                "image_tuning": {"profile": "normal"},
                "stream": {"source": "bad_source"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(config_path)


def test_next_image_tuning_profile_cycles() -> None:
    assert next_image_tuning_profile("normal") == "low_light"
    assert next_image_tuning_profile("low_light") == "bright_scene"
    assert next_image_tuning_profile("bright_scene") == "sharpness_safe"
    assert next_image_tuning_profile("sharpness_safe") == "disabled"
    assert next_image_tuning_profile("disabled") == "normal"


def test_persist_image_tuning_profile_updates_settings_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "settings.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "camera": {"width": 1920, "height": 1080, "fps": 25},
                "image_tuning": {"profile": "normal"},
                "stream": {"source": "usb_v4l2"},
            }
        ),
        encoding="utf-8",
    )

    saved = persist_image_tuning_profile(config_path, "bright_scene")
    assert saved == "bright_scene"

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["image_tuning"]["profile"] == "bright_scene"
    assert data["camera"] == {"width": 1920, "height": 1080, "fps": 25}
    assert data["stream"]["source"] == "usb_v4l2"
