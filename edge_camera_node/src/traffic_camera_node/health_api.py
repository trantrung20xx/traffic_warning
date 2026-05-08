from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging import Logger
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .config import AppConfig
from .state import NodeState


class HealthAPIServer:
    def __init__(
        self,
        config: AppConfig,
        state: NodeState,
        logger: Logger,
        set_stream_enabled_callback: Callable[[bool], bool],
        restart_service_callback: Callable[[], bool],
    ) -> None:
        self._config = config
        self._state = state
        self._logger = logger
        self._set_stream_enabled_callback = set_stream_enabled_callback
        self._restart_service_callback = restart_service_callback
        self._thread: threading.Thread | None = None
        self._server: ThreadingHTTPServer | None = None

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class HealthHandler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _send_cors_headers(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

            def _send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
                data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
                self.send_response(int(status))
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(data)

            def _authorize_restart(self, query_string: str) -> tuple[bool, str | None, HTTPStatus]:
                if not owner._config.health_api.allow_restart_endpoint:
                    return False, "Restart endpoint disabled", HTTPStatus.FORBIDDEN
                expected = owner._config.health_api.token
                query = parse_qs(query_string)
                token = query.get("token", [None])[0]
                if expected and token != expected:
                    return False, "Invalid token", HTTPStatus.UNAUTHORIZED
                return True, None, HTTPStatus.OK

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(int(HTTPStatus.NO_CONTENT))
                self._send_cors_headers()
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    self._send_json(owner._state.snapshot().to_health_dict())
                    return

                if parsed.path == "/identity":
                    self._send_json(owner._state.identity.to_dict())
                    return

                if parsed.path == "/stream/start":
                    authorized, detail, status = self._authorize_restart(parsed.query)
                    if not authorized:
                        self._send_json({"detail": detail or "Unauthorized"}, status=status)
                        return
                    if not owner._set_stream_enabled_callback(True):
                        self._send_json(
                            {"detail": "Start stream request rejected"},
                            status=HTTPStatus.CONFLICT,
                        )
                        return
                    self._send_json({"status": "accepted", "stream_enabled": True})
                    return

                if parsed.path == "/stream/stop":
                    authorized, detail, status = self._authorize_restart(parsed.query)
                    if not authorized:
                        self._send_json({"detail": detail or "Unauthorized"}, status=status)
                        return
                    if not owner._set_stream_enabled_callback(False):
                        self._send_json(
                            {"detail": "Stop stream request rejected"},
                            status=HTTPStatus.CONFLICT,
                        )
                        return
                    self._send_json({"status": "accepted", "stream_enabled": False})
                    return

                if parsed.path == "/restart-service":
                    authorized, detail, status = self._authorize_restart(parsed.query)
                    if not authorized:
                        self._send_json({"detail": detail or "Unauthorized"}, status=status)
                        return
                    if not owner._restart_service_callback():
                        self._send_json(
                            {"detail": "Restart request rejected"},
                            status=HTTPStatus.CONFLICT,
                        )
                        return
                    self._send_json({"status": "accepted"})
                    return

                self._send_json(
                    {"detail": "Not Found"},
                    status=HTTPStatus.NOT_FOUND,
                )

        return HealthHandler

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        handler = self._build_handler()
        self._server = ThreadingHTTPServer(
            (self._config.health_api.host, self._config.health_api.port),
            handler,
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.5},
            daemon=True,
        )
        self._thread.start()
        self._logger.info(
            "Health API started at http://%s:%s",
            self._config.health_api.host,
            self._config.health_api.port,
        )

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
