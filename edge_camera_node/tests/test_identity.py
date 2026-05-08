from __future__ import annotations

import json
from pathlib import Path

from traffic_camera_node.config import load_config
from traffic_camera_node.identity import (
    camera_id_from_mac,
    load_or_create_identity,
    mdns_hostname_from_mac,
    normalize_mac,
)


def _write_minimal_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "camera": {"width": 2560, "height": 1440, "fps": 25},
                "image_tuning": {"profile": "normal"},
                "gpio": {"enabled": True},
            }
        ),
        encoding="utf-8",
    )


def test_camera_id_and_mdns_from_mac() -> None:
    mac = normalize_mac("DC:A6:32:11:22:33")
    assert mac == "dca632112233"
    assert camera_id_from_mac(mac) == "cam_dca632112233"
    assert mdns_hostname_from_mac(mac) == "cam-dca632112233.local"


def test_runtime_identity_is_persistent(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "settings.json"
    _write_minimal_config(config_path)
    config = load_config(config_path)

    monkeypatch.setattr(
        "traffic_camera_node.identity.detect_mac_address",
        lambda preferred_interfaces: ("eth0", "dca632112233"),
    )
    monkeypatch.setattr(
        "traffic_camera_node.identity.read_machine_id",
        lambda: "machine-123",
    )
    monkeypatch.setattr(
        "traffic_camera_node.identity.is_port_in_use",
        lambda _port: False,
    )
    monkeypatch.setattr(
        "traffic_camera_node.identity.detect_ipv4",
        lambda preferred_interfaces: ("eth0", "192.168.1.50"),
    )

    identity_first = load_or_create_identity(config)
    identity_second = load_or_create_identity(config)

    assert identity_first == identity_second
    assert identity_first.camera_id == "cam_dca632112233"
    assert identity_first.mdns_hostname == "cam-dca632112233.local"
    assert identity_first.fallback_ip == "192.168.1.50"
