from __future__ import annotations

from app.managers.camera_manager import CameraManager


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
