from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.camera_runtime import has_camera_runtime_source
from app.schemas.camera import CameraConfig, CameraLocation


def _camera(rtsp_url: str) -> CameraConfig:
    return CameraConfig(
        camera_id="cam_test",
        rtsp_url=rtsp_url,
        camera_type="roadside",
        view_direction="north",
        location=CameraLocation(road_name="test road"),
        monitored_lanes=[1],
        frame_width=1920,
        frame_height=1080,
    )


def test_camera_runtime_source_rejects_empty_rtsp_url() -> None:
    assert not has_camera_runtime_source(_camera(""))
    assert not has_camera_runtime_source(_camera("   "))


def test_camera_runtime_source_accepts_non_empty_rtsp_url() -> None:
    assert has_camera_runtime_source(_camera("rtsp://camera.local:8554/live"))
