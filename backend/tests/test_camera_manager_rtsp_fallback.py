from __future__ import annotations

from app.managers.camera_manager import CameraManager
from app.schemas.camera import CameraConfig, CameraLocation


class _FakeEdgeDiscovery:
    def __init__(self, rows):
        self._rows = list(rows)

    def list_registry(self):
        return list(self._rows)


def _build_manager_with_registry(rows):
    manager = CameraManager.__new__(CameraManager)
    manager._edge_discovery = _FakeEdgeDiscovery(rows)
    return manager


def test_rtsp_fallback_matches_mdns_host_root():
    manager = _build_manager_with_registry(
        [
            {
                "camera_id": "edge_cam_01",
                "host": "10.10.10.5",
                "mdns_host": "cam-2ccf6788f9e5.local",
                "stream_path": "/cam_2ccf6788f9e5",
                "ip_address": "10.10.10.5",
            }
        ]
    )

    fallback_ip = manager._find_rtsp_host_fallback_ip(
        camera_id="cam_02",
        unresolved_host="cam-2ccf6788f9e5.local",
        stream_path="/unrelated_path",
    )

    assert fallback_ip == "10.10.10.5"


def test_rtsp_fallback_matches_normalized_stream_path():
    manager = _build_manager_with_registry(
        [
            {
                "camera_id": "edge_cam_02",
                "host": "10.10.10.8",
                "mdns_host": "cam-abcdef.local",
                "stream_path": "/cam_abcdef",
                "ip_address": "10.10.10.8",
            }
        ]
    )

    fallback_ip = manager._find_rtsp_host_fallback_ip(
        camera_id="cam_02",
        unresolved_host="unresolvable-host.local",
        stream_path="cam_abcdef",
    )

    assert fallback_ip == "10.10.10.8"


def test_rtsp_fallback_returns_none_when_no_registry_match():
    manager = _build_manager_with_registry(
        [
            {
                "camera_id": "edge_cam_03",
                "host": "10.10.10.9",
                "mdns_host": "cam-xyz.local",
                "stream_path": "/cam_xyz",
                "ip_address": "10.10.10.9",
            }
        ]
    )

    fallback_ip = manager._find_rtsp_host_fallback_ip(
        camera_id="cam_02",
        unresolved_host="another-host.local",
        stream_path="/another_stream",
    )

    assert fallback_ip is None


def test_build_browser_stream_urls_from_rtsp_url():
    urls = CameraManager._build_browser_stream_urls("rtsp://10.10.10.5:8593/cam_01")
    assert urls["stream_path"] == "/cam_01"
    assert urls["webrtc"]["enabled"] is True
    assert urls["webrtc"]["whep_url"] == "http://10.10.10.5:8889/cam_01/whep"
    assert urls["hls"]["m3u8_url"] == "http://10.10.10.5:8888/cam_01/index.m3u8"


def test_get_camera_stream_endpoints_uses_runtime_rtsp_url():
    manager = _build_manager_with_registry([])
    manager.cameras = [
        CameraConfig(
            camera_id="cam_01",
            rtsp_url="rtsp://cam-01.local:8593/cam_01",
            camera_type="intersection",
            view_direction=None,
            frame_width=1280,
            frame_height=720,
            location=CameraLocation(road_name="Road A"),
            monitored_lanes=[1],
        )
    ]
    manager._resolve_runtime_rtsp_url = lambda **kwargs: "rtsp://10.10.10.5:8593/cam_01"  # type: ignore[method-assign]
    manager._resolve_browser_rtsp_url = lambda **kwargs: "rtsp://10.10.10.5:8593/cam_01"  # type: ignore[method-assign]

    payload = manager.get_camera_stream_endpoints("cam_01")

    assert payload["camera_id"] == "cam_01"
    assert payload["runtime_rtsp_url"] == "rtsp://10.10.10.5:8593/cam_01"
    assert payload["browser_rtsp_url"] == "rtsp://10.10.10.5:8593/cam_01"
    assert payload["webrtc"]["whep_url"] == "http://10.10.10.5:8889/cam_01/whep"
    assert payload["hls"]["m3u8_url"] == "http://10.10.10.5:8888/cam_01/index.m3u8"
    assert payload["mjpeg"]["preview_url"] == "/api/cameras/cam_01/preview"


def test_resolve_browser_rtsp_url_prefers_fallback_ip_for_local_host():
    manager = _build_manager_with_registry(
        [
            {
                "camera_id": "cam_01",
                "host": "10.10.10.5",
                "mdns_host": "cam-01.local",
                "stream_path": "/cam_01",
                "ip_address": "10.10.10.5",
            }
        ]
    )
    resolved = manager._resolve_browser_rtsp_url(
        camera_id="cam_01",
        rtsp_url="rtsp://cam-01.local:8593/cam_01",
    )
    assert resolved == "rtsp://10.10.10.5:8593/cam_01"
