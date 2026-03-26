from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.managers.camera_manager import CameraManager
from app.schemas.events import TrackMessage, ViolationEvent


def create_ws_router(manager: CameraManager) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/tracks")
    async def ws_tracks(ws: WebSocket, camera_id: Optional[str] = None):
        await ws.accept()
        q: Optional[asyncio.Queue] = None  # type: ignore[name-defined]
        try:
            q = manager.create_track_listener()
            while True:
                msg: TrackMessage = await q.get()
                if camera_id and msg.camera_id != camera_id:
                    continue
                await ws.send_json(msg.model_dump())
        except WebSocketDisconnect:
            return
        finally:
            if q is not None:
                manager.remove_track_listener(q)  # type: ignore[arg-type]

    @router.websocket("/ws/violations")
    async def ws_violations(ws: WebSocket, camera_id: Optional[str] = None):
        await ws.accept()
        q = None
        try:
            q = manager.create_violation_listener()
            while True:
                ev: ViolationEvent = await q.get()
                if camera_id and ev.camera_id != camera_id:
                    continue
                await ws.send_json({"type": "violation", "event": ev.model_dump()})
        except WebSocketDisconnect:
            return
        finally:
            if q is not None:
                manager.remove_violation_listener(q)

    return router

