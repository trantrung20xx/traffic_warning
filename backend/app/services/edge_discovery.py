from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from urllib.request import Request, urlopen

try:
    from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf
except Exception as exc:  # pragma: no cover - phụ thuộc môi trường runtime
    ServiceBrowser = None  # type: ignore[assignment]
    ServiceInfo = None  # type: ignore[assignment]
    ServiceListener = object  # type: ignore[assignment]
    Zeroconf = None  # type: ignore[assignment]
    _ZEROCONF_IMPORT_ERROR: Exception | None = exc
else:
    _ZEROCONF_IMPORT_ERROR = None


EDGE_SERVICE_TYPE = "_traffic-node._tcp.local."


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_path(value: str | None, fallback: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return fallback
    return raw if raw.startswith("/") else f"/{raw}"


def _normalize_host(value: str | None) -> str:
    return str(value or "").strip().rstrip(".")


def _http_url(host: str, port: int, path: str) -> str:
    host_value = host
    if ":" in host_value and not host_value.startswith("["):
        host_value = f"[{host_value}]"
    return f"http://{host_value}:{port}{_normalize_path(path, '/')}"


def _safe_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed


def _decode_txt_properties(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    decoded: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, bytes):
            key_text = key.decode("utf-8", errors="ignore").strip()
        else:
            key_text = str(key).strip()
        if not key_text:
            continue
        if value is None:
            value_text = ""
        elif isinstance(value, bytes):
            value_text = value.decode("utf-8", errors="ignore")
        else:
            value_text = str(value)
        decoded[key_text] = value_text
    return decoded


class _EdgeServiceListener(ServiceListener):
    def __init__(self, owner: "EdgeDiscoveryService") -> None:
        self._owner = owner

    def add_service(self, _zc: Zeroconf, service_type: str, name: str) -> None:
        self._owner.refresh_service(service_type, name)

    def update_service(self, _zc: Zeroconf, service_type: str, name: str) -> None:
        self._owner.refresh_service(service_type, name)

    def remove_service(self, _zc: Zeroconf, _service_type: str, name: str) -> None:
        self._owner.mark_service_offline(name)


class EdgeDiscoveryService:
    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        service_type: str = EDGE_SERVICE_TYPE,
        request_timeout_s: float = 2.5,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._service_type = service_type
        self._request_timeout_s = max(0.5, float(request_timeout_s))
        self._lock = RLock()
        self._registry: dict[str, dict[str, Any]] = {}
        self._service_to_camera_id: dict[str, str] = {}
        self._zeroconf: Zeroconf | None = None
        self._browser: ServiceBrowser | None = None
        self._listener: _EdgeServiceListener | None = None
        self._running = False

    async def start(self) -> None:
        await asyncio.to_thread(self._start_sync)

    async def stop(self) -> None:
        await asyncio.to_thread(self._stop_sync)

    async def rescan(self) -> list[dict[str, Any]]:
        await asyncio.to_thread(self._rescan_sync)
        # Mạng mDNS có thể phản hồi chậm hơn 1 vòng RTT; đợi ngắn theo polling để
        # UI nhận được kết quả ổn định hơn ngay sau thao tác Rescan.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 3.0
        latest: list[dict[str, Any]] = []
        while True:
            latest = self.list_registry()
            if latest:
                return latest
            if loop.time() >= deadline:
                return latest
            await asyncio.sleep(0.2)

    def list_registry(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in sorted(self._registry.values(), key=lambda row: str(row.get("camera_id", "")))]

    def get_camera(self, camera_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._registry.get(camera_id)
            return dict(item) if item else None

    def refresh_service(self, service_type: str, service_name: str) -> None:
        with self._lock:
            zc = self._zeroconf
        if zc is None:
            return

        try:
            info = zc.get_service_info(service_type, service_name, timeout=2000)
        except Exception as exc:  # pragma: no cover - IO/zeroconf internals
            self._logger.warning("Failed to resolve service %s (%s): %s", service_name, service_type, exc)
            return
        if info is None:
            return

        item = self._build_registry_item(service_name=service_name, info=info)
        if not item:
            return

        with self._lock:
            camera_id = str(item["camera_id"])
            self._registry[camera_id] = item
            self._service_to_camera_id[service_name] = camera_id

    def mark_service_offline(self, service_name: str) -> None:
        with self._lock:
            camera_id = self._service_to_camera_id.pop(service_name, None)
            if not camera_id:
                return
            item = self._registry.get(camera_id)
            if not item:
                return
            updated = dict(item)
            updated["status"] = "offline"
            updated["last_seen"] = _utcnow_iso()
            self._registry[camera_id] = updated

    async def proxy_stream_action(self, camera_id: str, action: str) -> dict[str, Any]:
        action_name = action.strip().lower()
        if action_name not in {"start", "stop", "restart"}:
            raise ValueError(f"Unsupported stream action: {action}")

        item = self.get_camera(camera_id)
        if not item:
            raise KeyError(camera_id)
        if item.get("status") != "online":
            raise ConnectionError("edge camera is offline or unreachable")

        target_url = _http_url(
            host=str(item.get("host") or ""),
            port=_safe_int(item.get("api_port"), 0),
            path=f"/api/stream/{action_name}",
        )
        if not str(item.get("host") or "").strip() or _safe_int(item.get("api_port"), 0) <= 0:
            raise ConnectionError("edge camera is offline or unreachable")

        try:
            payload = await asyncio.to_thread(self._http_json_request, target_url, "POST")
        except Exception as exc:
            self._logger.warning("Stream action %s failed for %s at %s: %s", action_name, camera_id, target_url, exc)
            self._mark_camera_offline(camera_id)
            raise ConnectionError("edge camera is offline or unreachable") from exc

        self._mark_camera_online(camera_id)
        return {
            "ok": True,
            "camera_id": camera_id,
            "action": action_name,
            "edge_response": payload,
        }

    def _start_sync(self) -> None:
        if Zeroconf is None or ServiceBrowser is None:
            self._logger.warning(
                "Edge discovery disabled: zeroconf is unavailable (%s).",
                _ZEROCONF_IMPORT_ERROR,
            )
            return

        with self._lock:
            if self._running:
                return

        try:
            zeroconf = Zeroconf()
            listener = _EdgeServiceListener(self)
            browser = ServiceBrowser(zeroconf, self._service_type, listener)
        except Exception as exc:  # pragma: no cover - multicast/OS dependent
            self._logger.warning(
                "Edge discovery startup failed for service %s: %s",
                self._service_type,
                exc,
            )
            return

        with self._lock:
            self._zeroconf = zeroconf
            self._listener = listener
            self._browser = browser
            self._running = True

        self._logger.info("Edge discovery started. service_type=%s", self._service_type)

    def _stop_sync(self) -> None:
        with self._lock:
            browser = self._browser
            zeroconf = self._zeroconf
            self._browser = None
            self._listener = None
            self._zeroconf = None
            self._running = False

        if browser is not None:
            try:
                browser.cancel()
            except Exception:  # pragma: no cover - best effort cleanup
                pass
        if zeroconf is not None:
            try:
                zeroconf.close()
            except Exception:  # pragma: no cover - best effort cleanup
                pass

    def _rescan_sync(self) -> None:
        with self._lock:
            if not self._running or self._zeroconf is None:
                return
        # Khởi động lại browser để buộc gửi query PTR mới, giúp refresh cả node vừa online lại.
        self._stop_sync()
        self._start_sync()

    def _build_registry_item(self, *, service_name: str, info: ServiceInfo) -> dict[str, Any] | None:
        txt = _decode_txt_properties(getattr(info, "properties", {}))
        service_host = _normalize_host(getattr(info, "server", "") or txt.get("host"))
        txt_ip_address = _normalize_host(txt.get("ip_address"))
        parsed_addresses: list[str] = []
        try:
            parsed_addresses = list(info.parsed_addresses())  # type: ignore[call-arg]
        except Exception:
            parsed_addresses = []
        ip_address = next((address for address in parsed_addresses if address), None) or (txt_ip_address or None)
        probe_hosts: list[str] = []
        for candidate in (service_host, ip_address):
            normalized = _normalize_host(candidate)
            if normalized and normalized not in probe_hosts:
                probe_hosts.append(normalized)
        host = probe_hosts[0] if probe_hosts else ""

        api_port = _safe_int(getattr(info, "port", 0), 0)
        if not host or api_port <= 0:
            self._logger.warning(
                "Skip discovered service %s due to missing host/api_port. host=%s api_port=%s",
                service_name,
                host,
                api_port,
            )
            return None

        health_path = _normalize_path(txt.get("health_path"), "/api/health")
        identity_path = _normalize_path(txt.get("identity_path"), "/api/identity")
        identity_api_url = _http_url(host, api_port, identity_path)
        health_api_url = _http_url(host, api_port, health_path)

        identity_payload: dict[str, Any] | None = None
        status = "offline"
        probe_error: Exception | None = None
        reachable_host = host
        for probe_host in probe_hosts:
            candidate_identity_url = _http_url(probe_host, api_port, identity_path)
            try:
                identity_raw = self._http_json_request(candidate_identity_url, "GET")
                if isinstance(identity_raw, dict):
                    identity_payload = identity_raw
                status = "online"
                reachable_host = probe_host
                identity_api_url = candidate_identity_url
                health_api_url = _http_url(probe_host, api_port, health_path)
                break
            except Exception as exc:
                probe_error = exc
        if status != "online" and probe_error is not None:
            self._logger.warning(
                "Identity probe failed for %s hosts=%s: %s",
                service_name,
                probe_hosts,
                probe_error,
            )

        camera_id = str(
            (identity_payload or {}).get("camera_id")
            or txt.get("camera_id")
            or service_name.split(".", 1)[0]
        )
        node_id = str((identity_payload or {}).get("node_id") or txt.get("node_id") or "")
        mac_address = str((identity_payload or {}).get("mac_address") or txt.get("mac") or "")
        rtsp_port = _safe_int((identity_payload or {}).get("rtsp_port"), _safe_int(txt.get("rtsp_port"), 0))
        stream_path = _normalize_path(
            str((identity_payload or {}).get("stream_path") or txt.get("rtsp_path") or ""),
            f"/{camera_id}",
        )
        rtsp_url = str((identity_payload or {}).get("rtsp_url") or "")
        if (not rtsp_url or reachable_host != service_host) and rtsp_port > 0:
            rtsp_url = f"rtsp://{reachable_host}:{rtsp_port}/{stream_path.lstrip('/')}"

        return {
            "camera_id": camera_id,
            "node_id": node_id,
            "mac_address": mac_address,
            "host": reachable_host,
            "mdns_host": service_host,
            "ip_address": ip_address,
            "api_port": api_port,
            "rtsp_port": rtsp_port,
            "stream_path": stream_path,
            "rtsp_url": rtsp_url,
            "health_api_url": health_api_url,
            "identity_api_url": identity_api_url,
            "status": status,
            "last_seen": _utcnow_iso(),
        }

    def _mark_camera_online(self, camera_id: str) -> None:
        with self._lock:
            current = self._registry.get(camera_id)
            if not current:
                return
            updated = dict(current)
            updated["status"] = "online"
            updated["last_seen"] = _utcnow_iso()
            self._registry[camera_id] = updated

    def _mark_camera_offline(self, camera_id: str) -> None:
        with self._lock:
            current = self._registry.get(camera_id)
            if not current:
                return
            updated = dict(current)
            updated["status"] = "offline"
            updated["last_seen"] = _utcnow_iso()
            self._registry[camera_id] = updated

    def _http_json_request(self, url: str, method: str) -> dict[str, Any]:
        request = Request(url=url, method=method)
        request.add_header("Accept", "application/json")
        with urlopen(request, timeout=self._request_timeout_s) as response:
            payload = response.read()
        if not payload:
            return {}
        parsed = json.loads(payload.decode("utf-8"))
        if isinstance(parsed, dict):
            return parsed
        return {"data": parsed}
