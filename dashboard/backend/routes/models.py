"""
Model listing routes.

GET /api/models/openrouter          – full model list from OpenRouter
GET /api/models/openrouter/filtered – per-agent filtered lists
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

import httpx

from config import config

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/models", tags=["models"])

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


async def _fetch_openrouter_models(api_key: str) -> list[dict]:
    """
    Fetch raw model list from OpenRouter.
    Raises HTTPException on failure.
    """
    if not api_key:
        raise HTTPException(status_code=400, detail="OpenRouter API key is required")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(OPENROUTER_MODELS_URL, headers=headers)
    except httpx.RequestError as exc:
        log.error("openrouter_fetch_error", error=str(exc))
        raise HTTPException(status_code=502, detail=f"Failed to reach OpenRouter: {exc}") from exc

    if resp.status_code == 401:
        raise HTTPException(status_code=400, detail="Invalid OpenRouter API key")
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"OpenRouter returned HTTP {resp.status_code}: {resp.text[:200]}",
        )

    try:
        data = resp.json()
        return data.get("data", [])
    except Exception as exc:
        log.error("openrouter_parse_error", error=str(exc))
        raise HTTPException(status_code=502, detail="Failed to parse OpenRouter response") from exc


def _safe_prompt_price(model: dict) -> float:
    """Return prompt price per token as float, defaulting to large value if missing."""
    try:
        pricing = model.get("pricing") or {}
        val = pricing.get("prompt", None)
        if val is None:
            return 9999.0
        return float(val)
    except (TypeError, ValueError):
        return 9999.0


def _safe_context(model: dict) -> int:
    """Return context_length as int, defaulting to 0 if missing."""
    try:
        return int(model.get("context_length") or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/openrouter")
async def list_openrouter_models(key: Optional[str] = Query(default=None)):
    """
    Return the full model list from OpenRouter.
    Uses `key` query param if provided, otherwise falls back to OPENROUTER_API_KEY env var.
    """
    api_key = key or config.openrouter_api_key
    models = await _fetch_openrouter_models(api_key)
    return {"models": models, "count": len(models)}


@router.get("/openrouter/filtered")
async def list_openrouter_models_filtered(key: Optional[str] = Query(default=None)):
    """
    Return per-agent filtered model lists.

    Filter rules:
      supervisor : pricing.prompt < 0.000003  AND context_length >= 32000
      architect  : context_length >= 100000
      coder      : context_length >= 64000
      tester     : pricing.prompt < 0.000005
      reviewer   : context_length >= 100000

    Each list is sorted by pricing.prompt ascending.
    """
    api_key = key or config.openrouter_api_key
    models = await _fetch_openrouter_models(api_key)

    def _sort_key(m: dict) -> float:
        return _safe_prompt_price(m)

    supervisor = sorted(
        [
            m for m in models
            if _safe_prompt_price(m) < 0.000003 and _safe_context(m) >= 32000
        ],
        key=_sort_key,
    )
    architect = sorted(
        [m for m in models if _safe_context(m) >= 100000],
        key=_sort_key,
    )
    coder = sorted(
        [m for m in models if _safe_context(m) >= 64000],
        key=_sort_key,
    )
    tester = sorted(
        [m for m in models if _safe_prompt_price(m) < 0.000005],
        key=_sort_key,
    )
    reviewer = sorted(
        [m for m in models if _safe_context(m) >= 100000],
        key=_sort_key,
    )

    return {
        "supervisor": supervisor,
        "architect": architect,
        "coder": coder,
        "tester": tester,
        "reviewer": reviewer,
    }
