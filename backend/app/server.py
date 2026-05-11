from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import create_api_router
from app.api.ws import create_ws_router
from app.managers.camera_manager import CameraManager
from app.services.edge_discovery import EdgeDiscoveryService

if sys.platform.startswith("win") and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    # Trên Windows, ProactorEventLoop dễ in stack trace WinError 10054 khi trình duyệt
    # đóng tab hoặc ngắt websocket đột ngột. Selector policy ổn định hơn cho tải FastAPI/WebSocket này.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logger = logging.getLogger(__name__)


def _is_ignorable_windows_reset(context: dict[str, Any]) -> bool:
    if not sys.platform.startswith("win"):
        return False
    exc = context.get("exception")
    if not isinstance(exc, OSError):
        return False
    if getattr(exc, "winerror", None) != 10054:
        return False

    handle_repr = repr(context.get("handle") or "")
    if "_call_connection_lost" in handle_repr and "ProactorBasePipeTransport" in handle_repr:
        return True

    message = str(context.get("message") or "").lower()
    return "connection was forcibly closed by the remote host" in message


def _install_event_loop_exception_guard() -> None:
    """
    Trên Windows có thể xuất hiện callback benign khi client đóng socket đột ngột
    trong lúc shutdown, gây in traceback WinError 10054 dù backend vẫn dừng đúng.
    Guard này chỉ bỏ qua đúng case đó, còn lại vẫn chuyển cho default handler.
    """
    loop = asyncio.get_running_loop()
    if getattr(loop, "_traffic_warning_exception_guard_installed", False):
        return

    previous_handler = loop.get_exception_handler()

    def _handler(active_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        if _is_ignorable_windows_reset(context):
            return
        if previous_handler is not None:
            previous_handler(active_loop, context)
            return
        active_loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)
    setattr(loop, "_traffic_warning_exception_guard_installed", True)


def create_app() -> FastAPI:
    app = FastAPI(title="Traffic Warning Backend", version="0.1.0")

    # Mở CORS rộng để frontend chạy local gọi API thuận tiện trong lúc phát triển.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    repo_root = Path(__file__).resolve().parents[2]
    manager = CameraManager(repo_root)
    edge_discovery = EdgeDiscoveryService()
    manager.bind_edge_discovery(edge_discovery)
    app.state.manager = manager
    app.state.edge_discovery = edge_discovery

    app.include_router(create_api_router(manager, edge_discovery))
    app.include_router(create_ws_router(manager))

    @app.on_event("startup")
    async def _startup():
        _install_event_loop_exception_guard()
        await edge_discovery.start()
        rows: list[dict[str, Any]] = []
        try:
            # Warm registry sớm để runtime camera có thể dùng fallback IP thay cho host .local
            # trên các môi trường không resolve được mDNS (đặc biệt Windows).
            rows = await edge_discovery.rescan()
        except Exception as exc:
            # Discovery lỗi không được chặn pipeline AI core.
            logger.warning("Edge discovery rescan during startup failed: %s", exc)
        await manager.start()
        if rows:
            manager.refresh_runtime_sources_from_discovery(
                camera_ids={str(row.get("camera_id") or "") for row in rows}
            )

    @app.on_event("shutdown")
    async def _shutdown():
        await edge_discovery.stop()
        await manager.stop()
    return app


app = create_app()

