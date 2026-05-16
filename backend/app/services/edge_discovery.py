from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress
import json
import logging
import os
import socket
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
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


class EdgeStreamActionError(Exception):
    def __init__(self, *, status_code: int, message: str) -> None:
        self.status_code = int(status_code)
        self.message = str(message)
        super().__init__(self.message)


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
        fallback_probe_timeout_s: float = 0.45,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._service_type = service_type
        self._request_timeout_s = max(0.5, float(request_timeout_s))
        self._fallback_probe_timeout_s = max(0.2, float(fallback_probe_timeout_s))
        self._fallback_ports = self._load_fallback_ports()
        self._fallback_seeds = self._load_fallback_seeds()
        self._lock = RLock()
        self._registry: dict[str, dict[str, Any]] = {}
        self._service_to_camera_id: dict[str, str] = {}
        self._zeroconf: Zeroconf | None = None
        self._browser: ServiceBrowser | None = None
        self._listener: _EdgeServiceListener | None = None
        self._running = False
        self._last_scan_summary: dict[str, Any] = {
            "service_type": self._service_type,
            "fallback_ports": list(self._fallback_ports),
            "fallback_probe_count": 0,
            "fallback_found_count": 0,
            "last_scan_at": None,
            "zeroconf_available": Zeroconf is not None and ServiceBrowser is not None,
        }

    async def start(self) -> None:
        await asyncio.to_thread(self._start_sync)

    async def stop(self) -> None:
        await asyncio.to_thread(self._stop_sync)

    async def rescan(self) -> list[dict[str, Any]]:
        await asyncio.to_thread(self._rescan_sync)
        await self.refresh_status()
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

    async def refresh_status(self, camera_id: str | None = None) -> list[dict[str, Any]]:
        await asyncio.to_thread(self._refresh_status_sync, camera_id)
        return self.list_registry()

    def list_registry(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in sorted(self._registry.values(), key=lambda row: str(row.get("camera_id", "")))]

    def get_camera(self, camera_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._registry.get(camera_id)
            return dict(item) if item else None

    def debug_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._last_scan_summary,
                "running": self._running,
                "registry_count": len(self._registry),
                "service_count": len(self._service_to_camera_id),
                "zeroconf_import_error": str(_ZEROCONF_IMPORT_ERROR) if _ZEROCONF_IMPORT_ERROR else None,
            }

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
            updated["node_status"] = "OFFLINE"
            updated["stream_enabled"] = False
            updated["stream_running"] = False
            updated["last_checked"] = _utcnow_iso()
            self._registry[camera_id] = updated

    async def proxy_stream_action(self, camera_id: str, action: str) -> dict[str, Any]:
        action_name = action.strip().lower()
        if action_name not in {"start", "stop", "restart"}:
            raise ValueError(f"Unsupported stream action: {action}")

        payload = await self._proxy_edge_post_action(
            camera_id=camera_id,
            action_label=f"stream {action_name}",
            path=f"/api/stream/{action_name}",
        )
        self._apply_stream_action_hint(camera_id=camera_id, action_name=action_name, edge_payload=payload)
        await self._wait_for_stream_action_state(camera_id=camera_id, action_name=action_name)
        latest = self.get_camera(camera_id)
        return {
            "ok": True,
            "camera_id": camera_id,
            "action": action_name,
            "edge_response": payload,
            "camera": latest,
        }

    async def proxy_image_tuning_cycle(self, camera_id: str) -> dict[str, Any]:
        payload = await self._proxy_edge_post_action(
            camera_id=camera_id,
            action_label="image_tuning_cycle",
            path="/api/image-tuning/cycle",
        )
        self._apply_image_tuning_hint(camera_id=camera_id, edge_payload=payload)
        latest = self.get_camera(camera_id)
        return {
            "ok": True,
            "camera_id": camera_id,
            "action": "image_tuning_cycle",
            "edge_response": payload,
            "camera": latest,
        }

    async def _proxy_edge_post_action(
        self,
        *,
        camera_id: str,
        action_label: str,
        path: str,
    ) -> dict[str, Any]:
        item = self.get_camera(camera_id)
        if not item:
            raise KeyError(camera_id)
        candidate_urls = self._build_action_candidate_urls(item=item, path=path)
        if not candidate_urls:
            self._mark_camera_offline(camera_id)
            raise ConnectionError("edge camera is offline or unreachable")

        last_error: Exception | None = None
        for target_url in candidate_urls:
            try:
                payload = await asyncio.to_thread(self._http_json_request, target_url, "POST")
            except HTTPError as exc:
                reached = self._extract_host_port_from_url(target_url)
                if reached is not None:
                    self._mark_camera_online(
                        camera_id,
                        reached_host=reached[0],
                        reached_port=reached[1],
                    )
                raise EdgeStreamActionError(
                    status_code=int(getattr(exc, "code", 502) or 502),
                    message=self._extract_http_error_message(exc) or "edge action failed",
                ) from exc
            except Exception as exc:
                last_error = exc
                self._logger.warning(
                    "Action %s failed for %s at %s: %s",
                    action_label,
                    camera_id,
                    target_url,
                    exc,
                )
                continue

            reached = self._extract_host_port_from_url(target_url)
            reached_host = reached[0] if reached is not None else None
            reached_port = reached[1] if reached is not None else None
            self._mark_camera_online(
                camera_id,
                reached_host=reached_host,
                reached_port=reached_port,
            )
            return payload

        self._mark_camera_offline(camera_id)
        if last_error is not None:
            raise ConnectionError("edge camera is offline or unreachable") from last_error
        raise ConnectionError("edge camera is offline or unreachable")

    async def _wait_for_stream_action_state(self, *, camera_id: str, action_name: str) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (4.5 if action_name in {"start", "restart"} else 3.0)

        while True:
            item = self.get_camera(camera_id)
            if not item:
                return

            try:
                reachable = await asyncio.to_thread(self._probe_health_reachable, item)
            except Exception as exc:
                self._logger.warning(
                    "Failed to probe edge stream state after %s for %s: %s",
                    action_name,
                    camera_id,
                    exc,
                )
                reachable = None
            if reachable is not None:
                reached_host, reached_port, health_payload = reachable
                self._mark_camera_online(
                    camera_id,
                    reached_host=reached_host,
                    reached_port=reached_port,
                    health_payload=health_payload,
                )
                item = self.get_camera(camera_id) or item

            stream_running = item.get("stream_running") is True
            stream_enabled = item.get("stream_enabled") is True
            if action_name == "stop" and not stream_running and not stream_enabled:
                return
            if action_name in {"start", "restart"} and stream_running:
                return

            if loop.time() >= deadline:
                return
            await asyncio.sleep(0.35)

    def _apply_stream_action_hint(
        self,
        *,
        camera_id: str,
        action_name: str,
        edge_payload: dict[str, Any],
    ) -> None:
        with self._lock:
            current = self._registry.get(camera_id)
            if not current:
                return
            updated = dict(current)
            if action_name == "start":
                updated["stream_enabled"] = True
            elif action_name == "stop":
                updated["stream_enabled"] = False
                updated["stream_running"] = False
            elif action_name == "restart":
                updated["stream_enabled"] = True

            if isinstance(edge_payload, dict):
                if "stream_enabled" in edge_payload:
                    updated["stream_enabled"] = bool(edge_payload.get("stream_enabled"))
                if "stream_running" in edge_payload:
                    updated["stream_running"] = bool(edge_payload.get("stream_running"))
                if "fps_estimate" in edge_payload:
                    try:
                        updated["edge_fps"] = float(edge_payload.get("fps_estimate"))
                    except (TypeError, ValueError):
                        pass

            self._registry[camera_id] = updated

    def _apply_image_tuning_hint(self, *, camera_id: str, edge_payload: dict[str, Any]) -> None:
        with self._lock:
            current = self._registry.get(camera_id)
            if not current:
                return
            updated = dict(current)
            if isinstance(edge_payload, dict):
                profile = edge_payload.get("image_tuning_profile")
                if profile is not None:
                    updated["image_tuning_profile"] = str(profile).strip().lower()
                if "stream_enabled" in edge_payload:
                    updated["stream_enabled"] = bool(edge_payload.get("stream_enabled"))
                if "stream_running" in edge_payload:
                    updated["stream_running"] = bool(edge_payload.get("stream_running"))
                if "fps_estimate" in edge_payload:
                    try:
                        updated["edge_fps"] = float(edge_payload.get("fps_estimate"))
                    except (TypeError, ValueError):
                        pass
            self._registry[camera_id] = updated

    def _build_action_candidate_urls(self, *, item: dict[str, Any], path: str) -> list[str]:
        candidates: list[str] = []

        api_port = _safe_int(item.get("api_port"), 0)
        ip_address = _normalize_host(str(item.get("ip_address") or ""))
        if ip_address and api_port > 0:
            candidates.append(_http_url(ip_address, api_port, path))

        host = _normalize_host(str(item.get("host") or ""))
        if host and api_port > 0:
            host_url = _http_url(host, api_port, path)
            if host_url not in candidates:
                candidates.append(host_url)

        for url_field in ("identity_api_url", "health_api_url"):
            endpoint = str(item.get(url_field) or "").strip()
            parsed = self._extract_host_port_from_url(endpoint)
            if parsed is None:
                continue
            endpoint_host, endpoint_port = parsed
            if not endpoint_host or endpoint_port <= 0:
                continue
            endpoint_url = _http_url(endpoint_host, endpoint_port, path)
            if endpoint_url not in candidates:
                candidates.append(endpoint_url)

        return candidates

    def _extract_http_error_message(self, exc: HTTPError) -> str:
        payload_bytes = b""
        try:
            payload_bytes = exc.read() or b""
        except Exception:
            payload_bytes = b""

        if payload_bytes:
            try:
                parsed = json.loads(payload_bytes.decode("utf-8"))
                if isinstance(parsed, dict):
                    detail = parsed.get("detail") or parsed.get("message")
                    if detail:
                        return str(detail)
                    return json.dumps(parsed, ensure_ascii=False)
                return str(parsed)
            except Exception:
                text = payload_bytes.decode("utf-8", errors="ignore").strip()
                if text:
                    return text

        reason = str(getattr(exc, "reason", "") or "").strip()
        if reason:
            return reason
        return f"HTTP {int(getattr(exc, 'code', 502) or 502)}"

    def _extract_host_port_from_url(self, value: str) -> tuple[str, int] | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = urlparse(raw)
        except Exception:
            return None
        if not parsed.hostname:
            return None
        port = int(parsed.port or 0)
        if port <= 0:
            return None
        return _normalize_host(parsed.hostname), port

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
            can_restart_dns_sd = self._running and self._zeroconf is not None

        if can_restart_dns_sd:
            # Khởi động lại browser để buộc gửi query PTR mới, giúp refresh cả node vừa online lại.
            self._stop_sync()
            self._start_sync()
        else:
            self._start_sync()

        with self._lock:
            has_registry = bool(self._registry)
        if not has_registry:
            self._scan_identity_fallback()

    def _load_fallback_ports(self) -> tuple[int, ...]:
        raw = os.getenv("TRAFFIC_EDGE_DISCOVERY_PORTS") or os.getenv("EDGE_CAMERA_API_PORTS") or "8088"
        ports: list[int] = []
        for item in raw.split(","):
            try:
                port = int(item.strip())
            except ValueError:
                continue
            if port > 0 and port not in ports:
                ports.append(port)
        return tuple(ports or [8088])

    def _load_fallback_seeds(self) -> tuple[str, ...]:
        raw = os.getenv("TRAFFIC_EDGE_DISCOVERY_SEEDS") or os.getenv("EDGE_CAMERA_SEEDS") or ""
        seeds = [item.strip() for item in raw.split(",") if item.strip()]
        return tuple(dict.fromkeys(seeds))

    def _candidate_probe_targets(self) -> list[tuple[str, int]]:
        targets: list[tuple[str, int]] = []

        for seed in self._fallback_seeds:
            host, port = self._parse_seed(seed)
            if not host:
                continue
            ports = (port,) if port else self._fallback_ports
            for candidate_port in ports:
                self._append_target(targets, host, candidate_port)

        for host in self._local_subnet_hosts():
            for port in self._fallback_ports:
                self._append_target(targets, host, port)

        return targets

    def _parse_seed(self, seed: str) -> tuple[str, int | None]:
        value = seed.strip()
        if not value:
            return "", None
        if value.startswith("["):
            end = value.find("]")
            if end > 0:
                host = value[1:end]
                rest = value[end + 1 :]
                if rest.startswith(":"):
                    return host, _safe_int(rest[1:], 0) or None
                return host, None
        if value.count(":") == 1:
            host, raw_port = value.rsplit(":", 1)
            return host.strip(), _safe_int(raw_port, 0) or None
        return value, None

    def _append_target(self, targets: list[tuple[str, int]], host: str, port: int) -> None:
        target = (_normalize_host(host), int(port))
        if target[0] and target[1] > 0 and target not in targets:
            targets.append(target)

    def _local_subnet_hosts(self) -> list[str]:
        hosts: list[str] = []
        for ip_text, prefix in self._local_ipv4_addresses():
            try:
                ip = ipaddress.ip_address(ip_text)
            except ValueError:
                continue
            if ip.is_loopback or ip.is_link_local or ip.is_multicast:
                continue

            effective_prefix = prefix if prefix is not None and 16 <= prefix <= 30 else 24
            try:
                network = ipaddress.ip_network(f"{ip_text}/{effective_prefix}", strict=False)
            except ValueError:
                continue
            if network.num_addresses > 512:
                network = ipaddress.ip_network(f"{ip_text}/24", strict=False)
            for candidate in network.hosts():
                candidate_text = str(candidate)
                if candidate_text != ip_text and candidate_text not in hosts:
                    hosts.append(candidate_text)
        return hosts

    def _local_ipv4_addresses(self) -> list[tuple[str, int | None]]:
        rows: list[tuple[str, int | None]] = []
        try:
            import ifaddr

            for adapter in ifaddr.get_adapters():
                for ip in adapter.ips:
                    address = ip.ip[0] if isinstance(ip.ip, tuple) else ip.ip
                    if ":" in str(address):
                        continue
                    prefix = getattr(ip, "network_prefix", None)
                    rows.append((str(address), int(prefix) if prefix is not None else None))
        except Exception:
            pass

        try:
            hostname = socket.gethostname()
            for address in socket.gethostbyname_ex(hostname)[2]:
                if "." in address:
                    rows.append((address, None))
        except Exception:
            pass

        unique: list[tuple[str, int | None]] = []
        for row in rows:
            if row not in unique:
                unique.append(row)
        return unique

    def _scan_identity_fallback(self) -> None:
        targets = self._candidate_probe_targets()
        found: list[dict[str, Any]] = []
        if targets:
            with ThreadPoolExecutor(max_workers=min(64, max(4, len(targets)))) as executor:
                futures = {
                    executor.submit(self._probe_identity_target, host, port): (host, port)
                    for host, port in targets
                }
                for future in as_completed(futures):
                    try:
                        item = future.result()
                    except Exception:
                        item = None
                    if item is not None:
                        found.append(item)

        with self._lock:
            for item in found:
                self._registry[str(item["camera_id"])] = item
            self._last_scan_summary = {
                "service_type": self._service_type,
                "fallback_ports": list(self._fallback_ports),
                "fallback_probe_count": len(targets),
                "fallback_found_count": len(found),
                "last_scan_at": _utcnow_iso(),
                "zeroconf_available": Zeroconf is not None and ServiceBrowser is not None,
            }
        if found:
            self._logger.info("Edge fallback identity probe found %s node(s).", len(found))

    def _refresh_status_sync(self, camera_id: str | None = None) -> None:
        with self._lock:
            if camera_id:
                candidates = [dict(self._registry.get(camera_id) or {})]
            else:
                candidates = [dict(item) for item in self._registry.values()]
        candidates = [item for item in candidates if item.get("camera_id")]
        if not candidates:
            return

        online_map: dict[str, tuple[str, int, dict[str, Any]]] = {}
        offline_ids: list[str] = []

        with ThreadPoolExecutor(max_workers=min(16, max(4, len(candidates)))) as executor:
            futures = {
                executor.submit(self._probe_health_reachable, item): str(item.get("camera_id") or "")
                for item in candidates
            }
            for future in as_completed(futures):
                current_camera_id = futures[future]
                if not current_camera_id:
                    continue
                try:
                    reachable = future.result()
                except Exception:
                    reachable = None
                if reachable is None:
                    offline_ids.append(current_camera_id)
                    continue
                online_map[current_camera_id] = reachable

        for current_camera_id, (host, port, health_payload) in online_map.items():
            self._mark_camera_online(
                current_camera_id,
                reached_host=host,
                reached_port=port,
                health_payload=health_payload,
            )
        for current_camera_id in offline_ids:
            self._mark_camera_offline(current_camera_id)

    def _probe_health_reachable(self, item: dict[str, Any]) -> tuple[str, int, dict[str, Any]] | None:
        expected_camera_id = str(item.get("camera_id") or "").strip()
        for candidate_url in self._build_health_candidate_urls(item):
            parsed = self._extract_host_port_from_url(candidate_url)
            if parsed is None:
                continue
            host, port = parsed
            try:
                payload = self._http_json_request(
                    candidate_url,
                    "GET",
                    timeout_s=min(self._request_timeout_s, 1.2),
                )
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            payload_camera_id = str(payload.get("camera_id") or "").strip()
            if expected_camera_id and payload_camera_id and payload_camera_id != expected_camera_id:
                continue
            return host, port, payload
        return None

    def _build_health_candidate_urls(self, item: dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        health_path = "/api/health"
        api_port = _safe_int(item.get("api_port"), 0)
        host = _normalize_host(str(item.get("host") or ""))
        ip_address = _normalize_host(str(item.get("ip_address") or ""))

        if ip_address and api_port > 0:
            candidates.append(_http_url(ip_address, api_port, health_path))

        direct_health = str(item.get("health_api_url") or "").strip()
        if direct_health:
            parsed_direct = self._extract_host_port_from_url(direct_health)
            if parsed_direct is not None:
                direct_host, direct_port = parsed_direct
                direct_normalized = _http_url(direct_host, direct_port, health_path)
            else:
                direct_normalized = direct_health
            if direct_normalized not in candidates:
                candidates.append(direct_normalized)

        if host and api_port > 0:
            host_url = _http_url(host, api_port, health_path)
            if host_url not in candidates:
                candidates.append(host_url)

        identity_endpoint = str(item.get("identity_api_url") or "").strip()
        identity_host_port = self._extract_host_port_from_url(identity_endpoint)
        if identity_host_port is not None:
            identity_host, identity_port = identity_host_port
            identity_health = _http_url(identity_host, identity_port, health_path)
            if identity_health not in candidates:
                candidates.append(identity_health)

        return candidates

    def _probe_identity_target(self, host: str, port: int) -> dict[str, Any] | None:
        identity_url = _http_url(host, port, "/api/identity")
        try:
            identity = self._http_json_request(identity_url, "GET", timeout_s=self._fallback_probe_timeout_s)
        except Exception:
            return None
        if not isinstance(identity, dict) or not identity.get("camera_id"):
            return None
        return self._build_identity_probe_item(host=host, api_port=port, identity=identity)

    def _build_identity_probe_item(self, *, host: str, api_port: int, identity: dict[str, Any]) -> dict[str, Any]:
        now_iso = _utcnow_iso()
        camera_id = str(identity.get("camera_id") or "")
        mdns_host = _normalize_host(str(identity.get("mdns_hostname") or ""))
        rtsp_port = _safe_int(identity.get("rtsp_port"), 0)
        stream_path = _normalize_path(str(identity.get("stream_path") or ""), f"/{camera_id}")
        rtsp_host = mdns_host if mdns_host and self._hostname_resolves(mdns_host) else host
        rtsp_url = str(identity.get("rtsp_url") or "")
        if not rtsp_url or rtsp_host != mdns_host:
            rtsp_url = f"rtsp://{rtsp_host}:{rtsp_port}/{stream_path.lstrip('/')}" if rtsp_port > 0 else ""
        return {
            "camera_id": camera_id,
            "node_id": str(identity.get("node_id") or ""),
            "mac_address": str(identity.get("mac_address") or ""),
            "host": host,
            "mdns_host": mdns_host,
            "ip_address": host if self._is_ipv4(host) else str(identity.get("fallback_ip") or ""),
            "api_port": api_port,
            "rtsp_port": rtsp_port,
            "stream_path": stream_path,
            "rtsp_url": rtsp_url,
            "health_api_url": _http_url(host, api_port, "/api/health"),
            "identity_api_url": _http_url(host, api_port, "/api/identity"),
            "status": "online",
            "node_status": "ONLINE",
            "stream_enabled": None,
            "stream_running": None,
            "last_seen": now_iso,
            "last_checked": now_iso,
            "discovery_source": "identity_probe",
        }

    def _hostname_resolves(self, hostname: str) -> bool:
        try:
            socket.gethostbyname(hostname)
        except OSError:
            return False
        return True

    def _is_ipv4(self, value: str) -> bool:
        try:
            return isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address)
        except ValueError:
            return False

    def _build_registry_item(self, *, service_name: str, info: ServiceInfo) -> dict[str, Any] | None:
        now_iso = _utcnow_iso()
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
        if not ip_address:
            ip_from_identity = _normalize_host(str((identity_payload or {}).get("fallback_ip") or ""))
            if ip_from_identity:
                ip_address = ip_from_identity

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
            "node_status": None,
            "stream_enabled": None,
            "stream_running": None,
            "last_seen": now_iso,
            "last_checked": now_iso,
            "discovery_source": "dns_sd",
        }

    def _mark_camera_online(
        self,
        camera_id: str,
        *,
        reached_host: str | None = None,
        reached_port: int | None = None,
        health_payload: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            current = self._registry.get(camera_id)
            if not current:
                return
            updated = dict(current)
            host = _normalize_host(reached_host)
            port = int(reached_port or 0)
            if host:
                updated["host"] = host
                if self._is_ipv4(host):
                    updated["ip_address"] = host
            if port > 0:
                updated["api_port"] = port
                updated["health_api_url"] = _http_url(str(updated.get("host") or host), port, "/api/health")
                updated["identity_api_url"] = _http_url(str(updated.get("host") or host), port, "/api/identity")
            if health_payload:
                payload_status = str(health_payload.get("status") or "").strip()
                if payload_status:
                    updated["node_status"] = payload_status
                if "stream_enabled" in health_payload:
                    updated["stream_enabled"] = bool(health_payload.get("stream_enabled"))
                if "stream_running" in health_payload:
                    updated["stream_running"] = bool(health_payload.get("stream_running"))
                if "fps_estimate" in health_payload:
                    try:
                        updated["edge_fps"] = float(health_payload.get("fps_estimate"))
                    except (TypeError, ValueError):
                        pass
                for field in (
                    "mdns_status",
                    "temperature_c",
                    "cpu_percent",
                    "ram_percent",
                    "disk_percent",
                    "throttled_raw",
                    "undervoltage",
                    "last_error",
                    "restart_count",
                    "watchdog_latched",
                    "active_interface",
                    "uptime_s",
                ):
                    if field in health_payload:
                        updated[field] = health_payload.get(field)
                if "image_tuning_profile" in health_payload:
                    updated["image_tuning_profile"] = str(
                        health_payload.get("image_tuning_profile") or ""
                    ).strip().lower()
                if "service_version" in health_payload:
                    updated["service_version"] = str(health_payload.get("service_version") or "")
            updated["status"] = "online"
            now_iso = _utcnow_iso()
            updated["last_seen"] = now_iso
            updated["last_checked"] = now_iso
            self._registry[camera_id] = updated

    def _mark_camera_offline(self, camera_id: str) -> None:
        with self._lock:
            current = self._registry.get(camera_id)
            if not current:
                return
            updated = dict(current)
            updated["status"] = "offline"
            updated["node_status"] = "OFFLINE"
            updated["stream_enabled"] = False
            updated["stream_running"] = False
            updated["last_checked"] = _utcnow_iso()
            self._registry[camera_id] = updated

    def _http_json_request(self, url: str, method: str, timeout_s: float | None = None) -> dict[str, Any]:
        request = Request(url=url, method=method)
        request.add_header("Accept", "application/json")
        with urlopen(request, timeout=timeout_s or self._request_timeout_s) as response:
            payload = response.read()
        if not payload:
            return {}
        parsed = json.loads(payload.decode("utf-8"))
        if isinstance(parsed, dict):
            return parsed
        return {"data": parsed}
