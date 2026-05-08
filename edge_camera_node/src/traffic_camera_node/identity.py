from __future__ import annotations

import hashlib
import json
import socket
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import psutil

from .config import AppConfig


@dataclass(frozen=True)
class RuntimeIdentity:
    camera_id: str
    node_id: str
    mac_address: str
    interface: str
    mdns_hostname: str
    rtsp_port: int
    stream_path: str
    fallback_ip: str | None
    created_at: str

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "camera_id": self.camera_id,
            "node_id": self.node_id,
            "mac_address": self.mac_address,
            "interface": self.interface,
            "mdns_hostname": self.mdns_hostname,
            "rtsp_port": self.rtsp_port,
            "stream_path": self.stream_path,
            "fallback_ip": self.fallback_ip,
            "created_at": self.created_at,
        }


def normalize_mac(mac: str) -> str:
    normalized = "".join(ch for ch in mac.lower() if ch in "0123456789abcdef")
    if len(normalized) != 12:
        raise ValueError(f"Invalid MAC address: {mac}")
    return normalized


def camera_id_from_mac(normalized_mac: str) -> str:
    return f"cam_{normalized_mac}"


def mdns_hostname_from_mac(normalized_mac: str, domain: str = "local") -> str:
    clean_domain = domain.strip(".").lower() or "local"
    return f"cam-{normalized_mac}.{clean_domain}"


def stable_node_id(machine_id: str, normalized_mac: str) -> str:
    # node_id được tạo theo quy tắc cố định, không phụ thuộc giá trị ngẫu nhiên hay mốc thời gian.
    seed = f"{machine_id}|{normalized_mac}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def read_machine_id() -> str:
    machine_id_path = Path("/etc/machine-id")
    if machine_id_path.exists():
        return machine_id_path.read_text(encoding="utf-8").strip()
    return socket.gethostname()


def _is_mac_like(value: str) -> bool:
    try:
        normalize_mac(value)
        return True
    except ValueError:
        return False


def detect_mac_address(preferred_interfaces: tuple[str, ...]) -> tuple[str, str]:
    addrs = psutil.net_if_addrs()
    for interface in preferred_interfaces:
        for addr in addrs.get(interface, []):
            if _is_mac_like(addr.address):
                return interface, normalize_mac(addr.address)

    for interface, entries in addrs.items():
        if interface.startswith("lo"):
            continue
        for addr in entries:
            if _is_mac_like(addr.address):
                return interface, normalize_mac(addr.address)

    raise RuntimeError("Unable to detect MAC address from network interfaces.")


def detect_ipv4(preferred_interfaces: tuple[str, ...]) -> tuple[str | None, str | None]:
    addrs = psutil.net_if_addrs()
    for interface in preferred_interfaces:
        for addr in addrs.get(interface, []):
            if addr.family == socket.AF_INET:
                return interface, addr.address

    for interface, entries in addrs.items():
        if interface.startswith("lo"):
            continue
        for addr in entries:
            if addr.family == socket.AF_INET:
                return interface, addr.address

    return None, None


def is_port_in_use(port: int) -> bool:
    return bool(get_port_listeners(port))


def get_port_listeners(port: int) -> set[int]:
    listeners: set[int] = set()
    for conn in psutil.net_connections(kind="inet"):
        if conn.status != psutil.CONN_LISTEN:
            continue
        if not conn.laddr:
            continue
        if conn.laddr.port == port and conn.pid:
            listeners.add(conn.pid)
    return listeners


def allocate_rtsp_port(
    node_id: str,
    port_start: int,
    port_end: int,
    fixed_rtsp_port: int | None,
    port_checker: Callable[[int], bool] = is_port_in_use,
) -> int:
    if fixed_rtsp_port is not None:
        if port_checker(fixed_rtsp_port):
            raise RuntimeError(f"Configured fixed_rtsp_port {fixed_rtsp_port} is in use.")
        return fixed_rtsp_port

    window = port_end - port_start + 1
    offset = int(hashlib.sha256(node_id.encode("utf-8")).hexdigest(), 16) % window
    first_port = port_start + offset
    for index in range(window):
        candidate = port_start + ((first_port - port_start + index) % window)
        if not port_checker(candidate):
            return candidate

    raise RuntimeError(f"No free RTSP port in range {port_start}-{port_end}.")


def _identity_from_dict(raw: dict[str, object]) -> RuntimeIdentity:
    return RuntimeIdentity(
        camera_id=str(raw["camera_id"]),
        node_id=str(raw["node_id"]),
        mac_address=normalize_mac(str(raw["mac_address"])),
        interface=str(raw["interface"]),
        mdns_hostname=str(raw["mdns_hostname"]).lower(),
        rtsp_port=int(raw["rtsp_port"]),
        stream_path=str(raw["stream_path"]),
        fallback_ip=str(raw["fallback_ip"]) if raw.get("fallback_ip") else None,
        created_at=str(raw["created_at"]),
    )


def _load_identity(persist_file: Path) -> RuntimeIdentity | None:
    if not persist_file.exists():
        return None
    raw = json.loads(persist_file.read_text(encoding="utf-8"))
    required = {
        "camera_id",
        "node_id",
        "mac_address",
        "interface",
        "mdns_hostname",
        "rtsp_port",
        "stream_path",
        "created_at",
    }
    if not isinstance(raw, dict) or not required.issubset(raw.keys()):
        return None
    return _identity_from_dict(raw)


def _persist_identity(persist_file: Path, identity: RuntimeIdentity) -> None:
    persist_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = persist_file.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(identity.to_dict(), ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(persist_file)


def load_or_create_identity(config: AppConfig) -> RuntimeIdentity:
    persist_file = config.persist_identity_path
    existing = _load_identity(persist_file)
    if existing is not None:
        return existing

    # Toàn bộ ID ổn định được suy ra từ định danh phần cứng một lần rồi lưu lại.
    interface, mac = detect_mac_address(config.identity.preferred_interfaces)
    machine_id = read_machine_id()
    node_id = stable_node_id(machine_id, mac)
    rtsp_port = allocate_rtsp_port(
        node_id=node_id,
        port_start=config.identity.port_range_start,
        port_end=config.identity.port_range_end,
        fixed_rtsp_port=config.identity.fixed_rtsp_port,
    )

    camera_id = (config.identity.fixed_camera_id or camera_id_from_mac(mac)).strip()
    if not camera_id:
        raise ValueError("camera_id is empty after generation.")

    mdns_hostname = (
        config.identity.fixed_mdns_hostname
        or mdns_hostname_from_mac(mac, config.identity.mdns_domain)
    ).strip().lower()
    if not mdns_hostname:
        raise ValueError("mdns_hostname is empty after generation.")

    _, current_ip = detect_ipv4(config.identity.preferred_interfaces)
    identity = RuntimeIdentity(
        camera_id=camera_id,
        node_id=node_id,
        mac_address=mac,
        interface=interface,
        mdns_hostname=mdns_hostname,
        rtsp_port=rtsp_port,
        stream_path=f"/{camera_id}",
        fallback_ip=current_ip,
        created_at=datetime.now().astimezone().isoformat(),
    )
    _persist_identity(persist_file, identity)
    return identity


def persist_fallback_ip_if_missing(
    config: AppConfig,
    identity: RuntimeIdentity,
    detected_ip: str | None,
) -> RuntimeIdentity:
    # IP dự phòng chỉ ghi một lần để giữ ổn định qua các lần khởi động lại.
    if identity.fallback_ip or not detected_ip:
        return identity
    updated = RuntimeIdentity(
        camera_id=identity.camera_id,
        node_id=identity.node_id,
        mac_address=identity.mac_address,
        interface=identity.interface,
        mdns_hostname=identity.mdns_hostname,
        rtsp_port=identity.rtsp_port,
        stream_path=identity.stream_path,
        fallback_ip=detected_ip,
        created_at=identity.created_at,
    )
    _persist_identity(config.persist_identity_path, updated)
    return updated
