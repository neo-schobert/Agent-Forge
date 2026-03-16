"""
AgentForge Dashboard Backend
FastAPI application entry point.

- Runs on port 3020
- Serves React frontend from /app/frontend/dist
- Exposes /api/* routes
- WebSocket at /ws for real-time events
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import config
from routes import chat, forgejo, models, settings, system, tasks

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts events."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)
        log.info("ws_client_connected", total=len(self._clients))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)
        log.info("ws_client_disconnected", total=len(self._clients))

    async def broadcast(self, data: Any) -> None:
        """Send a JSON-serialisable payload to all connected clients."""
        if not self._clients:
            return
        message = json.dumps(data)
        dead: list[WebSocket] = []
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_text(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)

    async def send_personal(self, websocket: WebSocket, data: Any) -> None:
        """Send a JSON-serialisable payload to a single client."""
        await websocket.send_text(json.dumps(data))


# Singleton – imported by routes that need to broadcast
manager = ConnectionManager()

# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.reload()
    log.info(
        "agentforge_dashboard_starting",
        port=3020,
        llm_provider=config.llm_provider or "not set",
        forgejo_url=config.forgejo_base_url,
        orchestrator_url=config.orchestrator_url,
        langfuse_url=config.langfuse_base_url,
        is_configured=config.is_configured,
    )
    yield
    log.info("agentforge_dashboard_shutting_down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AgentForge Dashboard API",
        description="Backend API for the AgentForge multi-agent orchestration dashboard.",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS – allow everything in development; tighten for production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # API routers
    # ------------------------------------------------------------------
    app.include_router(models.router)
    app.include_router(system.router)
    app.include_router(tasks.router)
    app.include_router(forgejo.router)
    app.include_router(chat.router)
    app.include_router(settings.router)

    # ------------------------------------------------------------------
    # Health endpoint
    # ------------------------------------------------------------------

    @app.get("/api/health", tags=["health"])
    async def health():
        return {
            "status": "ok",
            "service": "agentforge-dashboard",
            "llm_provider": config.llm_provider or None,
            "is_configured": config.is_configured,
        }

    # ------------------------------------------------------------------
    # Global WebSocket hub (/ws)
    # ------------------------------------------------------------------

    @app.websocket("/ws")
    async def ws_hub(websocket: WebSocket):
        """
        Generic real-time event hub.
        Clients receive broadcast events (task updates, agent events, etc.).
        """
        await manager.connect(websocket)
        try:
            while True:
                # Keep connection alive; client can send ping/pong
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        msg = {"raw": raw}
                    # Echo back as acknowledgement
                    await manager.send_personal(websocket, {"type": "ack", "echo": msg})
                except asyncio.TimeoutError:
                    await manager.send_personal(websocket, {"type": "ping"})
        except WebSocketDisconnect:
            pass
        finally:
            await manager.disconnect(websocket)

    # ------------------------------------------------------------------
    # Static files – React frontend
    # ------------------------------------------------------------------
    frontend_dist = Path("/app/frontend/dist")

    if frontend_dist.exists():
        # Mount assets directory directly so hashed filenames are served
        assets_dir = frontend_dist / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            """
            Catch-all: serve index.html for client-side routing.
            Known static files are served directly.
            """
            # Avoid catching /api and /ws routes (they are registered first)
            if full_path.startswith("api/") or full_path.startswith("ws/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)

            candidate = frontend_dist / full_path
            if candidate.exists() and candidate.is_file():
                return FileResponse(str(candidate))
            index = frontend_dist / "index.html"
            if index.exists():
                return FileResponse(str(index))
            return JSONResponse({"detail": "Frontend not built"}, status_code=503)
    else:
        log.warning("frontend_dist_not_found", path=str(frontend_dist))

        @app.get("/", include_in_schema=False)
        async def no_frontend():
            return JSONResponse(
                {
                    "detail": "Frontend not built. Run `npm run build` inside dashboard/frontend.",
                    "api_docs": "/docs",
                }
            )

    return app


# ---------------------------------------------------------------------------
# Application instance (used by uvicorn)
# ---------------------------------------------------------------------------
app = create_app()

# Make the connection manager accessible to routes via app.state
app.state.manager = manager


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=3020,
        reload=os.environ.get("ENV", "production") != "production",
        log_config=None,  # use structlog
    )
