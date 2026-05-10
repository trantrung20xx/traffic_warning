from __future__ import annotations

import logging
import threading
from io import StringIO
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


class _ExitedProc:
    def __init__(self, code: int, stderr: str) -> None:
        self._code = code
        self.stderr = StringIO(stderr)

    def poll(self) -> int:
        return self._code

    def terminate(self) -> None:
        return

    def wait(self, timeout: float | None = None) -> int:
        return self._code

    def kill(self) -> None:
        return


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


def test_publish_returns_error_when_avahi_process_exits_early(monkeypatch) -> None:
    logger = logging.getLogger("test-mdns")
    publisher = MdnsPublisher(logger)

    def _fake_which(name: str) -> str | None:
        if name in {"avahi-publish", "avahi-publish-service"}:
            return f"/usr/bin/{name}"
        return None

    calls = {"count": 0}

    def _fake_popen(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return _ExitedProc(1, "daemon not running")
        return _DummyProc()

    monkeypatch.setattr("traffic_camera_node.network.shutil.which", _fake_which)
    monkeypatch.setattr("traffic_camera_node.network.subprocess.Popen", _fake_popen)

    status, detail = publisher.publish(
        hostname="cam-a.local",
        ip_address="192.168.1.11",
        api_port=8088,
        service_metadata=MdnsServiceMetadata(
            camera_id="cam_a",
            node_id="node_a",
            mac_address="001122334455",
            rtsp_port=8593,
            rtsp_path="/cam_a",
            ip_address="192.168.1.11",
        ),
    )

    assert status == "ERROR"
    assert detail is not None
    assert "avahi-publish" in detail
