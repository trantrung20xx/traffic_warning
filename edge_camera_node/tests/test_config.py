from __future__ import annotations

from pathlib import Path

import json
import pytest

from traffic_camera_node.config import load_config


def test_minimal_config_loads_with_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "settings.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "camera": {"width": 2560, "height": 1440, "fps": 25},
                "image_tuning": {"profile": "normal"},
                "gpio": {"enabled": True},
            }
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path)
    assert cfg.camera.width == 2560
    assert cfg.camera.height == 1440
    assert cfg.camera.fps == 25
    assert cfg.identity.port_range_start == 8554
    assert cfg.gpio.leds.online == 17


def test_invalid_profile_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "settings.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "camera": {"width": 2560, "height": 1440, "fps": 25},
                "image_tuning": {"profile": "impossible_profile"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(config_path)
