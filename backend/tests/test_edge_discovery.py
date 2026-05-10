from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.edge_discovery import EdgeDiscoveryService


class _FakeServiceInfo:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        properties: dict[bytes, bytes],
        addresses: list[str],
    ) -> None:
        self.server = host
        self.port = port
        self.properties = properties
        self._addresses = addresses

    def parsed_addresses(self) -> list[str]:
        return list(self._addresses)


def test_build_registry_item_prefers_identity_payload() -> None:
    discovery = EdgeDiscoveryService()

    info = _FakeServiceInfo(
        host="cam-2ccf6788f9e5.local.",
        port=8088,
        properties={
            b"camera_id": b"cam_2ccf6788f9e5",
            b"node_id": b"36be62afb543",
            b"mac": b"2ccf6788f9e5",
            b"rtsp_port": b"8593",
            b"rtsp_path": b"/cam_2ccf6788f9e5",
            b"health_path": b"/api/health",
            b"identity_path": b"/api/identity",
        },
        addresses=["172.20.10.2"],
    )

    discovery._http_json_request = lambda _url, _method: {  # type: ignore[method-assign]
        "camera_id": "cam_2ccf6788f9e5",
        "node_id": "36be62afb543",
        "mac_address": "2ccf6788f9e5",
        "mdns_hostname": "cam-2ccf6788f9e5.local",
        "api_port": 8088,
        "rtsp_port": 8593,
        "stream_path": "/cam_2ccf6788f9e5",
        "rtsp_url": "rtsp://cam-2ccf6788f9e5.local:8593/cam_2ccf6788f9e5",
        "health_api_url": "http://cam-2ccf6788f9e5.local:8088/api/health",
        "identity_api_url": "http://cam-2ccf6788f9e5.local:8088/api/identity",
    }

    item = discovery._build_registry_item(service_name="cam_2ccf6788f9e5._traffic-node._tcp.local.", info=info)

    assert item is not None
    assert item["camera_id"] == "cam_2ccf6788f9e5"
    assert item["host"] == "cam-2ccf6788f9e5.local"
    assert item["api_port"] == 8088
    assert item["rtsp_url"] == "rtsp://cam-2ccf6788f9e5.local:8593/cam_2ccf6788f9e5"
    assert item["status"] == "online"


def test_build_registry_item_falls_back_to_txt_when_identity_unreachable() -> None:
    discovery = EdgeDiscoveryService()
    info = _FakeServiceInfo(
        host="",
        port=8088,
        properties={
            b"camera_id": b"cam_fallback",
            b"node_id": b"node_fallback",
            b"mac": b"aa11bb22cc33",
            b"rtsp_port": b"8559",
            b"rtsp_path": b"/cam_fallback",
        },
        addresses=["172.20.10.99"],
    )

    def _raise_unreachable(_url: str, _method: str) -> dict:
        raise OSError("network unreachable")

    discovery._http_json_request = _raise_unreachable  # type: ignore[method-assign]
    item = discovery._build_registry_item(service_name="cam_fallback._traffic-node._tcp.local.", info=info)

    assert item is not None
    assert item["camera_id"] == "cam_fallback"
    assert item["host"] == "172.20.10.99"
    assert item["status"] == "offline"
    assert item["rtsp_url"] == "rtsp://172.20.10.99:8559/cam_fallback"

