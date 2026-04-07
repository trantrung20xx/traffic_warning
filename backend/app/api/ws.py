from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.managers.camera_manager import CameraManager
from app.schemas.events import TrackMessage, ViolationEvent


def create_ws_router(manager: CameraManager) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/tracks")
    async def ws_tracks(ws: WebSocket, camera_id: Optional[str] = None):
        await ws.accept()
        manager.register_track_websocket(ws)
        q: Optional[asyncio.Queue] = None  # type: ignore[name-defined]
        try:
            q = manager.create_track_listener()
            while True:
                msg = await q.get()
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
                ev = await q.get()
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

