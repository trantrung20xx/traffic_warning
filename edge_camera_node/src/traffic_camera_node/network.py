from __future__ import annotations

import shutil
import socket
import subprocess
import threading
from dataclasses import dataclass
from logging import Logger

import psutil

from .identity import RuntimeIdentity


@dataclass(frozen=True)
class NetworkInfo:
    ip_address: str | None
    interface: str | None


@dataclass(frozen=True)
class RtspUrls:
    primary_rtsp_url: str
    ip_fallback_rtsp_url: str | None


def detect_ipv4(preferred_interfaces: tuple[str, ...]) -> NetworkInfo:
    addrs = psutil.net_if_addrs()
    for interface in preferred_interfaces:
        for addr in addrs.get(interface, []):
            if addr.family == socket.AF_INET:
                return NetworkInfo(ip_address=addr.address, interface=interface)

    for interface, entries in addrs.items():
        if interface.startswith("lo"):
            continue
        for addr in entries:
            if addr.family == socket.AF_INET:
                return NetworkInfo(ip_address=addr.address, interface=interface)
    return NetworkInfo(ip_address=None, interface=None)


def build_rtsp_urls(identity: RuntimeIdentity, current_ip: str | None) -> RtspUrls:
    # Ưu tiên mDNS làm địa chỉ chính để giảm phụ thuộc DHCP.
    stream_path = identity.stream_path.lstrip("/")
    primary = f"rtsp://{identity.mdns_hostname}:{identity.rtsp_port}/{stream_path}"

    # URL dự phòng ưu tiên IP đã lưu trước đó để URL không đổi giữa các lần khởi động lại.
    fallback_ip = identity.fallback_ip or current_ip
    fallback = (
        f"rtsp://{fallback_ip}:{identity.rtsp_port}/{stream_path}"
        if fallback_ip
        else None
    )
    return RtspUrls(primary_rtsp_url=primary, ip_fallback_rtsp_url=fallback)


def probe_mdns(hostname: str) -> tuple[str, str | None]:
    avahi_resolve = shutil.which("avahi-resolve")
    if avahi_resolve:
        proc = subprocess.run(
            [avahi_resolve, "-n", hostname],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if proc.returncode == 0:
            return "OK", None
        detail = (proc.stderr or proc.stdout).strip() or "avahi-resolve failed"
        return "ERROR", detail

    try:
        socket.gethostbyname(hostname)
    except OSError as exc:
        return "ERROR", str(exc)
    return "OK", None


class MdnsPublisher:
    """Phát hostname mDNS ổn định qua avahi, không tự đổi chuỗi hostname."""

    def __init__(self, logger: Logger) -> None:
        self._logger = logger
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._published_hostname: str | None = None
        self._published_ip: str | None = None

    def publish(self, hostname: str, ip_address: str | None) -> tuple[str, str | None]:
        if ip_address is None:
            self.stop()
            return "ERROR", "No IPv4 address available"

        avahi_publish = shutil.which("avahi-publish")
        if not avahi_publish:
            return "ERROR", "avahi-publish command not found"

        with self._lock:
            if (
                self._process
                and self._process.poll() is None
                and hostname == self._published_hostname
                and ip_address == self._published_ip
            ):
                return "OK", None

            self._stop_unlocked()
            try:
                self._process = subprocess.Popen(
                    [avahi_publish, "-a", "-R", hostname, ip_address],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            except Exception as exc:  # pragma: no cover - phụ thuộc môi trường máy chạy
                self._process = None
                return "ERROR", str(exc)
            self._published_hostname = hostname
            self._published_ip = ip_address
            self._logger.info("Started mDNS publish: %s -> %s", hostname, ip_address)
            return "OK", None

    def _stop_unlocked(self) -> None:
        if not self._process:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None
        self._published_hostname = None
        self._published_ip = None

    def stop(self) -> None:
        with self._lock:
            self._stop_unlocked()
