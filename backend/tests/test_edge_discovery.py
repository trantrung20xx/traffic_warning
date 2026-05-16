from __future__ import annotations

import asyncio
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


def test_build_registry_item_falls_back_to_ip_when_mdns_host_unreachable() -> None:
    discovery = EdgeDiscoveryService()
    info = _FakeServiceInfo(
        host="cam-mdns-only.local.",
        port=8088,
        properties={
            b"camera_id": b"cam_probe",
            b"node_id": b"node_probe",
            b"mac": b"aabbccddee00",
            b"rtsp_port": b"8559",
            b"rtsp_path": b"/cam_probe",
        },
        addresses=["192.168.1.99"],
    )

    def _probe(url: str, _method: str) -> dict:
        if "cam-mdns-only.local" in url:
            raise OSError("name resolution failed")
        if "192.168.1.99" in url:
            return {
                "camera_id": "cam_probe",
                "node_id": "node_probe",
                "mac_address": "aabbccddee00",
                "rtsp_port": 8559,
                "stream_path": "/cam_probe",
            }
        raise AssertionError(f"unexpected url: {url}")

    discovery._http_json_request = _probe  # type: ignore[method-assign]
    item = discovery._build_registry_item(service_name="cam_probe._traffic-node._tcp.local.", info=info)

    assert item is not None
    assert item["camera_id"] == "cam_probe"
    assert item["status"] == "online"
    assert item["host"] == "192.168.1.99"
    assert item["mdns_host"] == "cam-mdns-only.local"
    assert item["rtsp_url"] == "rtsp://192.168.1.99:8559/cam_probe"


def test_identity_probe_item_uses_reachable_ip_for_api_urls(monkeypatch) -> None:
    discovery = EdgeDiscoveryService()
    monkeypatch.setattr(discovery, "_hostname_resolves", lambda _hostname: True)

    item = discovery._build_identity_probe_item(
        host="172.20.10.2",
        api_port=8088,
        identity={
            "camera_id": "cam_2ccf6788f9e5",
            "node_id": "36be62afb543",
            "mac_address": "2ccf6788f9e5",
            "mdns_hostname": "cam-2ccf6788f9e5.local",
            "rtsp_port": 8593,
            "stream_path": "/cam_2ccf6788f9e5",
            "rtsp_url": "rtsp://cam-2ccf6788f9e5.local:8593/cam_2ccf6788f9e5",
        },
    )

    assert item["camera_id"] == "cam_2ccf6788f9e5"
    assert item["host"] == "172.20.10.2"
    assert item["mdns_host"] == "cam-2ccf6788f9e5.local"
    assert item["identity_api_url"] == "http://172.20.10.2:8088/api/identity"
    assert item["rtsp_url"] == "rtsp://cam-2ccf6788f9e5.local:8593/cam_2ccf6788f9e5"
    assert item["discovery_source"] == "identity_probe"


def test_rescan_runs_fallback_even_when_dns_sd_is_not_running(monkeypatch) -> None:
    discovery = EdgeDiscoveryService()

    def _fake_scan_identity_fallback() -> None:
        discovery._registry["cam_probe"] = {
            "camera_id": "cam_probe",
            "host": "172.20.10.2",
            "api_port": 8088,
            "status": "online",
        }

    monkeypatch.setattr(discovery, "_start_sync", lambda: None)
    monkeypatch.setattr(discovery, "_scan_identity_fallback", _fake_scan_identity_fallback)
    monkeypatch.setattr(discovery, "_refresh_status_sync", lambda _camera_id=None: None)

    rows = asyncio.run(discovery.rescan())

    assert rows == [
        {
            "camera_id": "cam_probe",
            "host": "172.20.10.2",
            "api_port": 8088,
            "status": "online",
        }
    ]


def test_proxy_stream_action_attempts_even_when_registry_status_offline(monkeypatch) -> None:
    discovery = EdgeDiscoveryService()
    discovery._registry["cam_a"] = {
        "camera_id": "cam_a",
        "host": "cam-a.local",
        "ip_address": "172.20.10.2",
        "api_port": 8088,
        "identity_api_url": "http://172.20.10.2:8088/api/identity",
        "health_api_url": "http://172.20.10.2:8088/api/health",
        "status": "offline",
    }

    called_urls: list[str] = []

    def _fake_http_request(url: str, method: str, timeout_s: float | None = None) -> dict[str, object]:
        _ = timeout_s
        called_urls.append(url)
        if "cam-a.local" in url:
            raise OSError("name resolution failed")
        assert method == "POST"
        return {"status": "accepted"}

    monkeypatch.setattr(discovery, "_http_json_request", _fake_http_request)

    result = asyncio.run(discovery.proxy_stream_action("cam_a", "restart"))

    assert result["ok"] is True
    assert result["camera_id"] == "cam_a"
    assert result["action"] == "restart"
    assert discovery._registry["cam_a"]["status"] == "online"
    assert discovery._registry["cam_a"]["host"] == "172.20.10.2"
    assert any("172.20.10.2" in url for url in called_urls)


def test_proxy_image_tuning_cycle_updates_registry(monkeypatch) -> None:
    discovery = EdgeDiscoveryService()
    discovery._registry["cam_tuning"] = {
        "camera_id": "cam_tuning",
        "host": "172.20.10.9",
        "ip_address": "172.20.10.9",
        "api_port": 8088,
        "identity_api_url": "http://172.20.10.9:8088/api/identity",
        "health_api_url": "http://172.20.10.9:8088/api/health",
        "status": "online",
        "image_tuning_profile": "normal",
    }

    called_urls: list[str] = []

    def _fake_http_request(url: str, method: str, timeout_s: float | None = None) -> dict[str, object]:
        _ = timeout_s
        called_urls.append(url)
        assert method == "POST"
        return {
            "status": "accepted",
            "image_tuning_profile": "low_light",
            "stream_enabled": True,
            "stream_running": True,
        }

    monkeypatch.setattr(discovery, "_http_json_request", _fake_http_request)

    result = asyncio.run(discovery.proxy_image_tuning_cycle("cam_tuning"))

    assert result["ok"] is True
    assert result["camera_id"] == "cam_tuning"
    assert result["action"] == "image_tuning_cycle"
    assert discovery._registry["cam_tuning"]["image_tuning_profile"] == "low_light"
    assert any("/api/image-tuning/cycle" in url for url in called_urls)


def test_refresh_status_marks_camera_offline_when_health_unreachable(monkeypatch) -> None:
    discovery = EdgeDiscoveryService()
    discovery._registry["cam_dead"] = {
        "camera_id": "cam_dead",
        "host": "172.20.10.55",
        "ip_address": "172.20.10.55",
        "api_port": 8088,
        "health_api_url": "http://172.20.10.55:8088/api/health",
        "status": "online",
        "last_seen": "2026-01-01T00:00:00+00:00",
    }

    def _raise_unreachable(_url: str, _method: str, timeout_s: float | None = None) -> dict[str, object]:
        _ = timeout_s
        raise OSError("network unreachable")

    monkeypatch.setattr(discovery, "_http_json_request", _raise_unreachable)
    asyncio.run(discovery.refresh_status("cam_dead"))

    assert discovery._registry["cam_dead"]["status"] == "offline"
    assert discovery._registry["cam_dead"]["last_seen"] == "2026-01-01T00:00:00+00:00"


def test_refresh_status_treats_streaming_health_payload_as_online(monkeypatch) -> None:
    discovery = EdgeDiscoveryService()
    discovery._registry["cam_live"] = {
        "camera_id": "cam_live",
        "host": "172.20.10.66",
        "ip_address": "172.20.10.66",
        "api_port": 8088,
        "health_api_url": "http://172.20.10.66:8088/api/health",
        "status": "offline",
    }

    def _healthy_payload(_url: str, _method: str, timeout_s: float | None = None) -> dict[str, object]:
        _ = timeout_s
        return {
            "camera_id": "cam_live",
            "status": "STREAMING",
            "stream_enabled": True,
            "stream_running": True,
            "cpu_percent": 42.5,
            "ram_percent": 61.2,
            "disk_percent": 70.4,
            "temperature_c": 63.1,
            "undervoltage": False,
            "watchdog_latched": False,
            "restart_count": 2,
            "uptime_s": 1234,
            "active_interface": "eth0",
            "last_error": None,
            "throttled_raw": "0x0",
        }

    monkeypatch.setattr(discovery, "_http_json_request", _healthy_payload)
    asyncio.run(discovery.refresh_status("cam_live"))

    assert discovery._registry["cam_live"]["status"] == "online"
    assert discovery._registry["cam_live"]["node_status"] == "STREAMING"
    assert discovery._registry["cam_live"]["stream_enabled"] is True
    assert discovery._registry["cam_live"]["stream_running"] is True
    assert discovery._registry["cam_live"]["cpu_percent"] == 42.5
    assert discovery._registry["cam_live"]["ram_percent"] == 61.2
    assert discovery._registry["cam_live"]["disk_percent"] == 70.4
    assert discovery._registry["cam_live"]["temperature_c"] == 63.1
    assert discovery._registry["cam_live"]["undervoltage"] is False
    assert discovery._registry["cam_live"]["watchdog_latched"] is False
    assert discovery._registry["cam_live"]["restart_count"] == 2
    assert discovery._registry["cam_live"]["uptime_s"] == 1234
    assert discovery._registry["cam_live"]["active_interface"] == "eth0"
    assert discovery._registry["cam_live"]["throttled_raw"] == "0x0"
