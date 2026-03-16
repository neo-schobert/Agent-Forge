"""
AgentForge Orchestrator — Point d'entrée FastAPI
Reçoit les webhooks Forgejo et coordonne le cycle de vie des tâches agents.
"""

import asyncio
import os
import signal
import sys
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from webhook_handler import WebhookHandler
from git_manager import GitManager
from container_manager import ContainerManager
from task_monitor import TaskMonitor

# ---------------------------------------------------------------------------
# Logging structuré
# ---------------------------------------------------------------------------
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(__import__("logging"), os.getenv("LOG_LEVEL", "INFO"))
    ),
)
logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
class Config:
    FORGEJO_BASE_URL: str = os.getenv("FORGEJO_BASE_URL", "http://forgejo:3000")
    FORGEJO_API_TOKEN: str = os.getenv("FORGEJO_API_TOKEN", "")
    FORGEJO_ADMIN_USER: str = os.getenv("FORGEJO_ADMIN_USER", "admin")
    FORGEJO_ADMIN_PASS: str = os.getenv("FORGEJO_ADMIN_PASS", "changeme")
    FORGEJO_WEBHOOK_SECRET: str = os.getenv("FORGEJO_WEBHOOK_SECRET", "")
    FORGEJO_WORKSPACE_REPO: str = os.getenv("FORGEJO_WORKSPACE_REPO", "agentforge-workspace")

    LANGFUSE_HOST: str = os.getenv("LANGFUSE_HOST", "http://langfuse:3000")
    LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    LANGFUSE_SECRET_KEY_API: str = os.getenv("LANGFUSE_SECRET_KEY_API", "")

    AGENT_IMAGE: str = os.getenv("AGENT_IMAGE", "agentforge_agent_runtime:latest")
    PROXY_IMAGE: str = os.getenv("PROXY_IMAGE", "agentforge_proxy:latest")
    WORKSPACES_DIR: str = os.getenv("WORKSPACES_DIR", "/workspaces")
    TASK_TIMEOUT_SECONDS: int = int(os.getenv("TASK_TIMEOUT_SECONDS", "1800"))

    KATA_VCPUS: int = int(os.getenv("KATA_VCPUS", "2"))
    KATA_RAM_MB: int = int(os.getenv("KATA_RAM_MB", "2048"))
    KATA_DISK_GB: int = int(os.getenv("KATA_DISK_GB", "10"))
    # Lire KATA_AVAILABLE depuis l'env — "false" désactive Kata et bascule sur runc
    KATA_AVAILABLE: bool = os.getenv("KATA_AVAILABLE", "true").lower() not in ("false", "0", "no")

    ORCHESTRATOR_PORT: int = int(os.getenv("ORCHESTRATOR_PORT", "8000"))
    PROXY_PORT: int = int(os.getenv("PROXY_PORT", "8877"))


config = Config()

# ---------------------------------------------------------------------------
# Composants
# ---------------------------------------------------------------------------
git_manager = GitManager(config)
container_manager = ContainerManager(config)
task_monitor = TaskMonitor(config)
webhook_handler = WebhookHandler(config, git_manager, container_manager, task_monitor)


# ---------------------------------------------------------------------------
# Lifespan (remplace @app.on_event deprecated dans FastAPI 0.110+)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Démarrage ---
    logger.info(
        "orchestrator_started",
        port=config.ORCHESTRATOR_PORT,
        forgejo=config.FORGEJO_BASE_URL,
        kata_available=config.KATA_AVAILABLE,
        agent_image=config.AGENT_IMAGE,
    )
    # Vérifier la connexion Forgejo (non-bloquant si indisponible)
    asyncio.create_task(git_manager.check_connection())

    yield  # <- l'application tourne ici

    # --- Arrêt ---
    logger.info("orchestrator_shutdown")
    await container_manager.cleanup_all()


# ---------------------------------------------------------------------------
# Application FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(
    title="AgentForge Orchestrator",
    description="Orchestrateur d'agents IA — reçoit les webhooks Forgejo et pilote les microVMs",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    """Health check endpoint — utilisé par Docker et install.sh."""
    return {
        "status": "ok",
        "service": "agentforge-orchestrator",
        "kata_available": config.KATA_AVAILABLE,
        "forgejo_url": config.FORGEJO_BASE_URL,
    }


@app.post("/webhook")
async def forgejo_webhook(request: Request):
    """
    Endpoint principal — reçoit les webhooks de Forgejo.
    Déclenché sur : création d'issue avec le label 'agent-task'.
    """
    body = await request.body()
    headers = dict(request.headers)

    try:
        result = await webhook_handler.handle(body, headers)
        return JSONResponse(content=result, status_code=202)
    except ValueError as e:
        logger.warning("webhook_rejected", reason=str(e))
        return JSONResponse(content={"error": str(e)}, status_code=400)
    except Exception as e:
        logger.error("webhook_error", error=str(e), exc_info=True)
        return JSONResponse(content={"error": "internal error"}, status_code=500)


@app.get("/tasks")
async def list_tasks():
    """Lister les tâches en cours."""
    return {
        "active_tasks": task_monitor.get_active_tasks(),
        "count": len(task_monitor.get_active_tasks()),
    }


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Obtenir le statut d'une tâche spécifique."""
    task = task_monitor.get_task(task_id)
    if task is None:
        return JSONResponse(content={"error": "task not found"}, status_code=404)
    return task


# ---------------------------------------------------------------------------
# Gestion des signaux
# ---------------------------------------------------------------------------
def handle_signal(signum, frame):
    logger.info("signal_received", signum=signum)
    sys.exit(0)


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("starting_orchestrator", port=config.ORCHESTRATOR_PORT)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=config.ORCHESTRATOR_PORT,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        access_log=True,
    )
