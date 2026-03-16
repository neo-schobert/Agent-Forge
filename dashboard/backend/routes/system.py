"""
System status and configuration routes.

GET /api/system/status  – health check for all services
GET /api/system/config  – current non-secret configuration
GET /api/system/tasks   – proxy to orchestrator task list
"""
from __future__ import annotations

import time
import structlog
from fastapi import APIRouter, HTTPException

import httpx

from config import config

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/system", tags=["system"])


async def _probe(client: httpx.AsyncClient, url: str, label: str) -> dict:
    """
    Perform a single HTTP GET health probe.
    Returns {status, latency_ms, details}.
    """
    start = time.monotonic()
    try:
        resp = await client.get(url, timeout=5)
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        if resp.status_code < 500:
            return {"status": "ok", "latency_ms": latency_ms, "details": None}
        return {
            "status": "error",
            "latency_ms": latency_ms,
            "details": f"HTTP {resp.status_code}",
        }
    except httpx.RequestError as exc:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        log.warning("probe_failed", service=label, error=str(exc))
        return {"status": "error", "latency_ms": latency_ms, "details": str(exc)}


@router.get("/status")
async def system_status():
    """Check health of Forgejo, LangFuse and the Orchestrator."""
    async with httpx.AsyncClient() as client:
        forgejo_probe = await _probe(client, f"{config.forgejo_base_url}/", "forgejo")
        langfuse_probe = await _probe(client, f"{config.langfuse_base_url}/api/public/health", "langfuse")
        orchestrator_probe = await _probe(client, f"{config.orchestrator_url}/health", "orchestrator")

    return {
        "forgejo": forgejo_probe,
        "langfuse": langfuse_probe,
        "orchestrator": orchestrator_probe,
    }


@router.get("/config")
async def system_config():
    """Return current non-secret configuration."""
    return {
        "llm_provider": config.llm_provider,
        "llm_model": config.llm_model,
        "agent_models": {
            "supervisor": config.agent_supervisor_model,
            "architect": config.agent_architect_model,
            "coder": config.agent_coder_model,
            "tester": config.agent_tester_model,
            "reviewer": config.agent_reviewer_model,
        },
        "forgejo_url": config.forgejo_base_url,
        "langfuse_url": config.langfuse_base_url,
        "is_configured": config.is_configured,
    }


@router.get("/tasks")
async def system_tasks():
    """Proxy GET /tasks to the orchestrator."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{config.orchestrator_url}/tasks")
    except httpx.RequestError as exc:
        log.error("orchestrator_tasks_error", error=str(exc))
        raise HTTPException(status_code=502, detail=f"Orchestrator unreachable: {exc}") from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Orchestrator error: {resp.text[:200]}",
        )

    return resp.json()
