from __future__ import annotations

from app.schemas.camera import CameraConfig


def has_camera_runtime_source(camera: CameraConfig) -> bool:
    """Return True only when a camera has a usable video source configured."""
    return bool(str(camera.rtsp_url or "").strip())
