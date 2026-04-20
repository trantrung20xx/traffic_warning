from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.managers.camera_manager import CameraManager
from app.schemas.events import TrackMessage, ViolationEvent


def create_ws_router(manager: CameraManager) -> APIRouter:
    router = APIRouter()

    async def _wait_for_queue_or_disconnect(ws: WebSocket, q: asyncio.Queue):
        """Chờ bản tin mới hoặc phát hiện client đã ngắt kết nối, tùy cái nào đến trước."""
        queue_task = asyncio.create_task(q.get())
        receive_task = asyncio.create_task(ws.receive())
        done, pending = await asyncio.wait(
            {queue_task, receive_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

        if receive_task in done:
            message = receive_task.result()
            if message.get("type") == "websocket.disconnect":
                if not queue_task.done():
                    queue_task.cancel()
                return "disconnect", None
            if queue_task.done():
                return "queue", queue_task.result()
            return "noop", None

        return "queue", queue_task.result()

    @router.websocket("/ws/tracks")
    async def ws_tracks(ws: WebSocket, camera_id: Optional[str] = None):
        await ws.accept()
        manager.register_track_websocket(ws)
        q: Optional[asyncio.Queue] = None  # type: ignore[name-defined]
        try:
            q = manager.create_track_listener()
            while True:
                event_type, payload = await _wait_for_queue_or_disconnect(ws, q)
                if event_type == "disconnect":
                    break
                if event_type != "queue":
                    continue
                msg = payload
                if msg is None:
                    break
                if camera_id and msg.camera_id != camera_id:
                    continue
                await ws.send_json(msg.model_dump(mode="json"))
        except asyncio.CancelledError:
            raise
        except WebSocketDisconnect:
            return
        finally:
            if q is not None:
                manager.remove_track_listener(q)  # type: ignore[arg-type]
            manager.unregister_track_websocket(ws)
            if ws.application_state != WebSocketState.DISCONNECTED:
                try:
                    await ws.close(code=1001, reason="Server shutting down")
                except Exception:
                    pass

    @router.websocket("/ws/violations")
    async def ws_violations(ws: WebSocket, camera_id: Optional[str] = None):
        await ws.accept()
        manager.register_violation_websocket(ws)
        q = None
        try:
            q = manager.create_violation_listener()
            while True:
                event_type, payload = await _wait_for_queue_or_disconnect(ws, q)
                if event_type == "disconnect":
                    break
                if event_type != "queue":
                    continue
                ev = payload
                if ev is None:
                    break
                if camera_id and ev.camera_id != camera_id:
                    continue
                await ws.send_json({"type": "violation", "event": ev.model_dump(mode="json")})
        except asyncio.CancelledError:
            raise
        except WebSocketDisconnect:
            return
        finally:
            if q is not None:
                manager.remove_violation_listener(q)
            manager.unregister_violation_websocket(ws)
            if ws.application_state != WebSocketState.DISCONNECTED:
                try:
                    await ws.close(code=1001, reason="Server shutting down")
                except Exception:
                    pass

    return router

