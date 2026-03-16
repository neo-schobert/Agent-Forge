"""
Settings routes.

GET  /api/settings                   – current settings (keys masked)
POST /api/settings                   – update settings (writes to .env + /run/secrets/)
POST /api/settings/test-provider     – test LLM provider connectivity
POST /api/settings/verify-openrouter – verify an OpenRouter key
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import httpx
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import config

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mask_key(key: str, visible: int = 4) -> str:
    """Return a partially masked key string, e.g. sk-ant-...xxxx"""
    if not key:
        return ""
    if len(key) <= visible * 2:
        return key[:visible] + "..." + "*" * 4
    return key[:visible] + "..." + key[-visible:]


def _read_env_file(path: str) -> dict[str, str]:
    """Parse a .env file into a dict (key=value pairs, ignores comments)."""
    result: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return result
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _write_env_file(path: str, data: dict[str, str]) -> None:
    """
    Update or create a .env file.
    Existing keys are updated in-place; new keys are appended.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    updated_keys: set[str] = set()

    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                lines.append(line)
                continue
            k = stripped.split("=", 1)[0].strip()
            if k in data:
                lines.append(f'{k}="{data[k]}"')
                updated_keys.add(k)
            else:
                lines.append(line)

    # Append keys not yet in the file
    for k, v in data.items():
        if k not in updated_keys:
            lines.append(f'{k}="{v}"')

    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_secret(name: str, value: str) -> None:
    """Write a value to /run/secrets/<name>."""
    secrets_dir = Path(config.secrets_path)
    try:
        secrets_dir.mkdir(parents=True, exist_ok=True)
        (secrets_dir / name).write_text(value, encoding="utf-8")
    except OSError as exc:
        log.warning("secret_write_failed", name=name, error=str(exc))


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class AgentModelsUpdate(BaseModel):
    supervisor: Optional[str] = None
    architect: Optional[str] = None
    coder: Optional[str] = None
    tester: Optional[str] = None
    reviewer: Optional[str] = None


class SettingsUpdate(BaseModel):
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    agent_models: Optional[AgentModelsUpdate] = None


class TestProviderBody(BaseModel):
    provider: str
    api_key: str
    model: Optional[str] = None


class VerifyOpenRouterBody(BaseModel):
    api_key: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def get_settings():
    """Return current settings with API keys partially masked."""
    config.reload()
    return {
        "llm_provider": config.llm_provider,
        "llm_model": config.llm_model,
        "anthropic_api_key_set": bool(config.anthropic_api_key),
        "anthropic_api_key_preview": _mask_key(config.anthropic_api_key) if config.anthropic_api_key else "",
        "openai_api_key_set": bool(config.openai_api_key),
        "openai_api_key_preview": _mask_key(config.openai_api_key) if config.openai_api_key else "",
        "openrouter_api_key_set": bool(config.openrouter_api_key),
        "openrouter_api_key_preview": _mask_key(config.openrouter_api_key) if config.openrouter_api_key else "",
        "agent_models": {
            "supervisor": config.agent_supervisor_model,
            "architect": config.agent_architect_model,
            "coder": config.agent_coder_model,
            "tester": config.agent_tester_model,
            "reviewer": config.agent_reviewer_model,
        },
        "forgejo_configured": bool(
            config.forgejo_api_token or (config.forgejo_admin_user and config.forgejo_admin_pass)
        ),
        "is_configured": config.is_configured,
    }


@router.post("")
async def update_settings(payload: SettingsUpdate):
    """
    Persist updated settings to the .env file and /run/secrets/.
    Returns the new settings (masked).
    """
    env_updates: dict[str, str] = {}

    if payload.llm_provider is not None:
        env_updates["LLM_PROVIDER"] = payload.llm_provider
        os.environ["LLM_PROVIDER"] = payload.llm_provider

    if payload.llm_model is not None:
        env_updates["LLM_MODEL"] = payload.llm_model
        os.environ["LLM_MODEL"] = payload.llm_model

    if payload.anthropic_api_key is not None:
        env_updates["ANTHROPIC_API_KEY"] = payload.anthropic_api_key
        os.environ["ANTHROPIC_API_KEY"] = payload.anthropic_api_key
        _write_secret("anthropic_api_key", payload.anthropic_api_key)
        # Also write generic secret for agent containers
        provider = payload.llm_provider or config.llm_provider
        if provider and provider.lower() == "anthropic":
            _write_secret("llm_api_key", payload.anthropic_api_key)

    if payload.openai_api_key is not None:
        env_updates["OPENAI_API_KEY"] = payload.openai_api_key
        os.environ["OPENAI_API_KEY"] = payload.openai_api_key
        _write_secret("openai_api_key", payload.openai_api_key)
        provider = payload.llm_provider or config.llm_provider
        if provider and provider.lower() == "openai":
            _write_secret("llm_api_key", payload.openai_api_key)

    if payload.openrouter_api_key is not None:
        env_updates["OPENROUTER_API_KEY"] = payload.openrouter_api_key
        os.environ["OPENROUTER_API_KEY"] = payload.openrouter_api_key
        _write_secret("openrouter_api_key", payload.openrouter_api_key)
        provider = payload.llm_provider or config.llm_provider
        if provider and provider.lower() == "openrouter":
            _write_secret("llm_api_key", payload.openrouter_api_key)

    if payload.agent_models:
        role_map = {
            "supervisor": "AGENT_SUPERVISOR_MODEL",
            "architect": "AGENT_ARCHITECT_MODEL",
            "coder": "AGENT_CODER_MODEL",
            "tester": "AGENT_TESTER_MODEL",
            "reviewer": "AGENT_REVIEWER_MODEL",
        }
        for role, env_key in role_map.items():
            value = getattr(payload.agent_models, role, None)
            if value is not None:
                env_updates[env_key] = value
                os.environ[env_key] = value

    if env_updates:
        try:
            _write_env_file(config.env_file_path, env_updates)
        except OSError as exc:
            log.warning("env_file_write_failed", error=str(exc))

    config.reload()
    return await get_settings()


@router.post("/test-provider")
async def test_provider(payload: TestProviderBody):
    """
    Make a minimal API call to verify the provider key works.
    Returns {success, error?, model_count?}
    """
    provider = payload.provider.lower()
    api_key = payload.api_key

    if not api_key:
        raise HTTPException(status_code=400, detail="api_key is required")

    try:
        if provider == "anthropic":
            model = payload.model or "claude-3-haiku-20240307"
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model,
                        "max_tokens": 10,
                        "messages": [{"role": "user", "content": "ping"}],
                    },
                )
            if resp.status_code == 200:
                return {"success": True}
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

        elif provider == "openai":
            model = payload.model or "gpt-4o-mini"
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "max_tokens": 5,
                        "messages": [{"role": "user", "content": "ping"}],
                    },
                )
            if resp.status_code == 200:
                return {"success": True}
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

        elif provider == "openrouter":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            if resp.status_code == 200:
                models = resp.json().get("data", [])
                return {"success": True, "model_count": len(models)}
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")

    except httpx.RequestError as exc:
        return {"success": False, "error": f"Network error: {exc}"}


@router.post("/verify-openrouter")
async def verify_openrouter(payload: VerifyOpenRouterBody):
    """Verify an OpenRouter key and return the number of available models."""
    if not payload.api_key:
        raise HTTPException(status_code=400, detail="api_key is required")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {payload.api_key}"},
            )
    except httpx.RequestError as exc:
        return {"valid": False, "model_count": 0, "error": str(exc)}

    if resp.status_code == 200:
        try:
            models = resp.json().get("data", [])
            return {"valid": True, "model_count": len(models), "error": None}
        except Exception:  # noqa: BLE001
            return {"valid": False, "model_count": 0, "error": "Failed to parse response"}

    return {
        "valid": False,
        "model_count": 0,
        "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
    }
