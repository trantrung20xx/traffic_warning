from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import create_api_router
from app.api.ws import create_ws_router
from app.managers.camera_manager import CameraManager


def create_app() -> FastAPI:
    app = FastAPI(title="Traffic Warning Backend", version="0.1.0")

    # For local UI dev
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    repo_root = Path(__file__).resolve().parents[2]
    manager = CameraManager(repo_root)
    app.state.manager = manager

    app.include_router(create_api_router(manager))
    app.include_router(create_ws_router(manager))

    @app.on_event("startup")
    async def _startup():
        await manager.start()

    @app.on_event("shutdown")
    async def _shutdown():
        await manager.stop()
    return app


app = create_app()

