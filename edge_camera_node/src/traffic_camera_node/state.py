from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .identity import RuntimeIdentity


class NodeStatus(str, Enum):
    BOOTING = "BOOTING"
    ONLINE = "ONLINE"
    STREAMING = "STREAMING"
    WARNING = "WARNING"
    ERROR = "ERROR"
    SHUTTING_DOWN = "SHUTTING_DOWN"


_ALLOWED_TRANSITIONS: dict[NodeStatus, set[NodeStatus]] = {
    NodeStatus.BOOTING: {NodeStatus.ONLINE, NodeStatus.ERROR, NodeStatus.SHUTTING_DOWN},
    NodeStatus.ONLINE: {
        NodeStatus.STREAMING,
        NodeStatus.WARNING,
        NodeStatus.ERROR,
        NodeStatus.SHUTTING_DOWN,
    },
    NodeStatus.STREAMING: {
        NodeStatus.ONLINE,
        NodeStatus.WARNING,
        NodeStatus.ERROR,
        NodeStatus.SHUTTING_DOWN,
    },
    NodeStatus.WARNING: {
        NodeStatus.ONLINE,
        NodeStatus.STREAMING,
        NodeStatus.ERROR,
        NodeStatus.SHUTTING_DOWN,
    },
    NodeStatus.ERROR: {NodeStatus.BOOTING, NodeStatus.ONLINE, NodeStatus.SHUTTING_DOWN},
    NodeStatus.SHUTTING_DOWN: set(),
}


@dataclass(frozen=True)
class HealthSnapshot:
    camera_id: str
    mdns_hostname: str
    primary_rtsp_url: str
    ip_address: str | None
    ip_fallback_rtsp_url: str | None
    mdns_status: str
    stream_enabled: bool
    stream_running: bool
    image_tuning_profile: str
    temperature_c: float | None
    uptime_s: int
    status: str
    fps_estimate: float
    cpu_percent: float | None
    ram_percent: float | None
    disk_percent: float | None
    throttled_raw: str | None
    undervoltage: bool | None
    last_error: str | None
    restart_count: int
    watchdog_latched: bool
    active_interface: str | None
    service_version: str

    def to_health_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "mdns_hostname": self.mdns_hostname,
            "primary_rtsp_url": self.primary_rtsp_url,
            "ip_address": self.ip_address,
            "ip_fallback_rtsp_url": self.ip_fallback_rtsp_url,
            "mdns_status": self.mdns_status,
            "stream_enabled": self.stream_enabled,
            "stream_running": self.stream_running,
            "image_tuning_profile": self.image_tuning_profile,
            "temperature_c": self.temperature_c,
            "uptime_s": self.uptime_s,
            "status": self.status,
            "fps_estimate": self.fps_estimate,
            "cpu_percent": self.cpu_percent,
            "ram_percent": self.ram_percent,
            "disk_percent": self.disk_percent,
            "throttled_raw": self.throttled_raw,
            "undervoltage": self.undervoltage,
            "last_error": self.last_error,
            "restart_count": self.restart_count,
            "watchdog_latched": self.watchdog_latched,
            "active_interface": self.active_interface,
            "service_version": self.service_version,
        }


class NodeState:
    def __init__(
        self,
        identity: RuntimeIdentity,
        image_tuning_profile: str,
        service_version: str,
    ) -> None:
        self._lock = threading.Lock()
        self._started_monotonic = time.monotonic()

        self._identity = identity
        self._image_tuning_profile = image_tuning_profile
        self._service_version = service_version

        self._status = NodeStatus.BOOTING
        self._stream_running = False
        self._fps_estimate = 0.0
        self._last_error: str | None = None
        self._restart_count = 0
        self._watchdog_latched = False
        self._mdns_status = "UNKNOWN"
        self._stream_enabled = True
        self._primary_rtsp_url = ""
        self._ip_address: str | None = None
        self._ip_fallback_rtsp_url: str | None = None
        self._active_interface: str | None = None
        self._temperature_c: float | None = None
        self._cpu_percent: float | None = None
        self._ram_percent: float | None = None
        self._disk_percent: float | None = None
        self._throttled_raw: str | None = None
        self._undervoltage: bool | None = None

    @property
    def identity(self) -> RuntimeIdentity:
        return self._identity

    def update_identity(self, identity: RuntimeIdentity) -> None:
        with self._lock:
            self._identity = identity

    def transition(self, next_status: NodeStatus) -> bool:
        with self._lock:
            if next_status == self._status:
                return True
            if next_status not in _ALLOWED_TRANSITIONS[self._status]:
                return False
            self._status = next_status
            return True

    def set_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message
            self._status = NodeStatus.ERROR

    def clear_error(self) -> None:
        with self._lock:
            self._last_error = None
            self._watchdog_latched = False
            if self._status == NodeStatus.ERROR:
                self._status = NodeStatus.ONLINE

    def set_watchdog_latched(self, latched: bool) -> None:
        with self._lock:
            self._watchdog_latched = latched
            if latched:
                self._status = NodeStatus.ERROR

    def set_stream_running(self, running: bool) -> None:
        with self._lock:
            self._stream_running = running
            if running and self._status in {NodeStatus.BOOTING, NodeStatus.ONLINE}:
                self._status = NodeStatus.STREAMING
            if not running and self._status == NodeStatus.STREAMING:
                self._status = NodeStatus.WARNING

    def set_stream_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._stream_enabled = enabled

    def set_image_tuning_profile(self, profile: str) -> None:
        with self._lock:
            self._image_tuning_profile = str(profile).strip().lower()

    def set_urls(
        self,
        primary_rtsp_url: str,
        ip_address: str | None,
        ip_fallback_rtsp_url: str | None,
        interface: str | None,
    ) -> None:
        with self._lock:
            self._primary_rtsp_url = primary_rtsp_url
            self._ip_address = ip_address
            self._ip_fallback_rtsp_url = ip_fallback_rtsp_url
            self._active_interface = interface

    def set_mdns_status(self, status: str, detail: str | None = None) -> None:
        with self._lock:
            self._mdns_status = status
            if detail and status == "ERROR":
                self._last_error = detail
                if self._status != NodeStatus.ERROR:
                    self._status = NodeStatus.WARNING

    def set_metrics(
        self,
        *,
        temperature_c: float | None,
        cpu_percent: float | None,
        ram_percent: float | None,
        disk_percent: float | None = None,
        throttled_raw: str | None = None,
        undervoltage: bool | None = None,
    ) -> None:
        with self._lock:
            self._temperature_c = temperature_c
            self._cpu_percent = cpu_percent
            self._ram_percent = ram_percent
            self._disk_percent = disk_percent
            self._throttled_raw = throttled_raw
            self._undervoltage = undervoltage

    def set_fps_estimate(self, fps: float) -> None:
        with self._lock:
            self._fps_estimate = max(0.0, float(fps))

    def set_restart_count(self, restart_count: int) -> None:
        with self._lock:
            self._restart_count = max(0, int(restart_count))

    def set_warning(self, message: str) -> None:
        with self._lock:
            self._last_error = message
            if self._status not in {NodeStatus.ERROR, NodeStatus.SHUTTING_DOWN}:
                self._status = NodeStatus.WARNING

    def get_status(self) -> NodeStatus:
        with self._lock:
            return self._status

    def snapshot(self) -> HealthSnapshot:
        with self._lock:
            uptime_s = int(time.monotonic() - self._started_monotonic)
            return HealthSnapshot(
                camera_id=self._identity.camera_id,
                mdns_hostname=self._identity.mdns_hostname,
                primary_rtsp_url=self._primary_rtsp_url,
                ip_address=self._ip_address,
                ip_fallback_rtsp_url=self._ip_fallback_rtsp_url,
                mdns_status=self._mdns_status,
                stream_enabled=self._stream_enabled,
                stream_running=self._stream_running,
                image_tuning_profile=self._image_tuning_profile,
                temperature_c=self._temperature_c,
                uptime_s=uptime_s,
                status=self._status.value,
                fps_estimate=self._fps_estimate,
                cpu_percent=self._cpu_percent,
                ram_percent=self._ram_percent,
                disk_percent=self._disk_percent,
                throttled_raw=self._throttled_raw,
                undervoltage=self._undervoltage,
                last_error=self._last_error,
                restart_count=self._restart_count,
                watchdog_latched=self._watchdog_latched,
                active_interface=self._active_interface,
                service_version=self._service_version,
            )
