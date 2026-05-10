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


TRAFFIC_NODE_SERVICE_TYPE = "_traffic-node._tcp.local."


@dataclass(frozen=True)
class MdnsServiceMetadata:
    camera_id: str
    node_id: str
    mac_address: str
    rtsp_port: int
    rtsp_path: str
    health_path: str = "/api/health"
    identity_path: str = "/api/identity"
    api_version: str = "1"
    service_type: str = TRAFFIC_NODE_SERVICE_TYPE

    @property
    def service_name(self) -> str:
        return self.camera_id

    def txt_records(self) -> tuple[str, ...]:
        return (
            f"camera_id={self.camera_id}",
            f"node_id={self.node_id}",
            f"mac={self.mac_address}",
            f"api_version={self.api_version}",
            f"rtsp_port={self.rtsp_port}",
            f"rtsp_path={self.rtsp_path}",
            f"health_path={self.health_path}",
            f"identity_path={self.identity_path}",
        )


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
    """Phát hostname mDNS và DNS-SD service qua avahi."""

    def __init__(self, logger: Logger) -> None:
        self._logger = logger
        self._host_process: subprocess.Popen[str] | None = None
        self._service_process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._published_hostname: str | None = None
        self._published_ip: str | None = None
        self._published_service_signature: tuple[str, ...] | None = None

    def publish(
        self,
        *,
        hostname: str,
        ip_address: str | None,
        api_port: int,
        service_metadata: MdnsServiceMetadata,
    ) -> tuple[str, str | None]:
        if ip_address is None:
            self.stop()
            return "ERROR", "No IPv4 address available"

        avahi_publish = shutil.which("avahi-publish")
        if not avahi_publish:
            return "ERROR", "avahi-publish command not found"
        if api_port <= 0:
            return "ERROR", f"Invalid API port: {api_port}"

        avahi_publish_service = shutil.which("avahi-publish-service")
        if not avahi_publish_service:
            return "ERROR", "avahi-publish-service command not found"

        with self._lock:
            if (
                self._host_process
                and self._host_process.poll() is None
                and hostname == self._published_hostname
                and ip_address == self._published_ip
                and self._service_process
                and self._service_process.poll() is None
                and self._published_service_signature
                == self._service_signature(hostname, api_port, service_metadata)
            ):
                return "OK", None

            self._stop_unlocked()
            try:
                self._host_process = subprocess.Popen(
                    [avahi_publish, "-a", "-R", hostname, ip_address],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                service_type_for_publish = service_metadata.service_type.rstrip(".")
                if service_type_for_publish.endswith(".local"):
                    service_type_for_publish = service_type_for_publish[: -len(".local")]
                service_args = [
                    avahi_publish_service,
                    "-H",
                    hostname,
                    service_metadata.service_name,
                    service_type_for_publish,
                    str(api_port),
                    *service_metadata.txt_records(),
                ]
                self._service_process = subprocess.Popen(
                    service_args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            except Exception as exc:  # pragma: no cover - phụ thuộc môi trường máy chạy
                self._host_process = None
                self._service_process = None
                return "ERROR", str(exc)
            self._published_hostname = hostname
            self._published_ip = ip_address
            self._published_service_signature = self._service_signature(
                hostname=hostname,
                api_port=api_port,
                service_metadata=service_metadata,
            )
            self._logger.info("Started mDNS publish: %s -> %s", hostname, ip_address)
            self._logger.info(
                "Started DNS-SD service: %s name=%s host=%s port=%s",
                service_metadata.service_type.rstrip("."),
                service_metadata.service_name,
                hostname,
                api_port,
            )
            return "OK", None

    def _service_signature(
        self,
        hostname: str,
        api_port: int,
        service_metadata: MdnsServiceMetadata,
    ) -> tuple[str, ...]:
        return (
            hostname,
            str(api_port),
            service_metadata.service_name,
            service_metadata.service_type,
            *service_metadata.txt_records(),
        )

    def _stop_unlocked(self) -> None:
        self._terminate_process(self._service_process)
        self._terminate_process(self._host_process)
        self._service_process = None
        self._host_process = None
        self._published_hostname = None
        self._published_ip = None
        self._published_service_signature = None

    def _terminate_process(self, process: subprocess.Popen[str] | None) -> None:
        if not process:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()

    def stop(self) -> None:
        with self._lock:
            self._stop_unlocked()
