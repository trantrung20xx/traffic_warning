from __future__ import annotations

import logging
import threading
from types import SimpleNamespace

from traffic_camera_node.network import MdnsPublisher


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
    publisher._process = existing  # type: ignore[attr-defined]
    publisher._published_hostname = "cam-a.local"  # type: ignore[attr-defined]
    publisher._published_ip = "192.168.1.10"  # type: ignore[attr-defined]

    def _fake_which(name: str) -> str | None:
        if name == "avahi-publish":
            return "/usr/bin/avahi-publish"
        return None

    def _fake_popen(*_args, **_kwargs):
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr("traffic_camera_node.network.shutil.which", _fake_which)
    monkeypatch.setattr("traffic_camera_node.network.subprocess.Popen", _fake_popen)

    result: dict[str, object] = {}

    def _call_publish() -> None:
        result["value"] = publisher.publish("cam-a.local", "192.168.1.11")

    thread = threading.Thread(target=_call_publish, daemon=True)
    thread.start()
    thread.join(timeout=1.0)

    assert not thread.is_alive(), "publish() should return quickly and must not deadlock"
    assert result["value"] == ("OK", None)
    assert existing.terminated is True
