from __future__ import annotations

import logging
import threading
from types import SimpleNamespace

from traffic_camera_node.network import MdnsPublisher, MdnsServiceMetadata


class _DummyProc:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True


def test_publish_replaces_existing_process_without_deadlock(monkeypatch) -> None:
    logger = logging.getLogger("test-mdns")
    publisher = MdnsPublisher(logger)
    existing = _DummyProc()
    publisher._host_process = existing  # type: ignore[attr-defined]
    publisher._service_process = existing  # type: ignore[attr-defined]
    publisher._published_hostname = "cam-a.local"  # type: ignore[attr-defined]
    publisher._published_ip = "192.168.1.10"  # type: ignore[attr-defined]

    def _fake_which(name: str) -> str | None:
        if name in {"avahi-publish", "avahi-publish-service"}:
            return "/usr/bin/avahi-publish"
        return None

    def _fake_popen(*_args, **_kwargs):
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr("traffic_camera_node.network.shutil.which", _fake_which)
    monkeypatch.setattr("traffic_camera_node.network.subprocess.Popen", _fake_popen)

    result: dict[str, object] = {}

    def _call_publish() -> None:
        result["value"] = publisher.publish(
            hostname="cam-a.local",
            ip_address="192.168.1.11",
            api_port=8088,
            service_metadata=MdnsServiceMetadata(
                camera_id="cam_a",
                node_id="node_a",
                mac_address="001122334455",
                rtsp_port=8593,
                rtsp_path="/cam_a",
            ),
        )

    thread = threading.Thread(target=_call_publish, daemon=True)
    thread.start()
    thread.join(timeout=1.0)

    assert not thread.is_alive(), "publish() should return quickly and must not deadlock"
    assert result["value"] == ("OK", None)
    assert existing.terminated is True
