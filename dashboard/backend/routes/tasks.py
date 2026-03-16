"""
Task management routes.

GET  /api/tasks                   – merged task list (orchestrator + Forgejo issues)
GET  /api/tasks/{task_id}         – single task details
WS   /ws/tasks/{task_id}/logs     – stream task logs
POST /api/tasks                   – create new task (creates Forgejo issue)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import structlog
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from config import config

log = structlog.get_logger(__name__)
router = APIRouter(tags=["tasks"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AGENT_TASK_LABEL = "agent-task"


def _forgejo_headers() -> dict:
    return {**config.forgejo_auth_headers(), "Content-Type": "application/json"}


def _parse_issue_to_task(issue: dict) -> dict:
    """Convert a Forgejo issue dict to a normalised task dict."""
    labels = [lbl.get("name", "") for lbl in (issue.get("labels") or [])]
    return {
        "id": str(issue.get("number", "")),
        "issue_number": issue.get("number"),
        "title": issue.get("title", ""),
        "status": issue.get("state", "open"),
        "branch": None,
        "created_at": issue.get("created_at"),
        "pr_url": None,
        "error": None,
        "agent_states": None,
        "labels": labels,
        "source": "forgejo",
    }


def _merge_tasks(orchestrator_tasks: list[dict], forgejo_tasks: list[dict]) -> list[dict]:
    """
    Merge two lists deduplicating by issue_number.
    Orchestrator data takes precedence over Forgejo data for shared keys.
    """
    merged: dict[Any, dict] = {}

    for task in forgejo_tasks:
        key = task.get("issue_number")
        if key is not None:
            merged[key] = task

    for task in orchestrator_tasks:
        key = task.get("issue_number")
        if key is not None and key in merged:
            merged[key] = {**merged[key], **task, "source": "both"}
        else:
            # Use task id as fallback key
            fallback = task.get("id", id(task))
            merged[f"orch_{fallback}"] = task

    return list(merged.values())


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/tasks")
async def list_tasks():
    """Return merged task list from orchestrator and Forgejo issues."""
    orchestrator_tasks: list[dict] = []
    forgejo_tasks: list[dict] = []

    async with httpx.AsyncClient(timeout=10) as client:
        # 1. Active tasks from orchestrator
        try:
            resp = await client.get(f"{config.orchestrator_url}/tasks")
            if resp.status_code == 200:
                data = resp.json()
                orchestrator_tasks = data if isinstance(data, list) else data.get("tasks", [])
        except httpx.RequestError as exc:
            log.warning("orchestrator_tasks_unreachable", error=str(exc))

        # 2. Historical issues from Forgejo
        try:
            owner = config.forgejo_admin_user or "agentforge"
            repo = config.forgejo_workspace_repo
            url = (
                f"{config.forgejo_api_base}/repos/{owner}/{repo}/issues"
                "?limit=20&type=issues&state=open"
            )
            resp = await client.get(url, headers=_forgejo_headers())
            if resp.status_code == 200:
                forgejo_tasks = [_parse_issue_to_task(i) for i in resp.json()]
        except httpx.RequestError as exc:
            log.warning("forgejo_issues_unreachable", error=str(exc))

    return {"tasks": _merge_tasks(orchestrator_tasks, forgejo_tasks)}


@router.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """Get single task details from orchestrator and Forgejo."""
    result: dict = {"id": task_id}

    async with httpx.AsyncClient(timeout=10) as client:
        # Try orchestrator first
        try:
            resp = await client.get(f"{config.orchestrator_url}/tasks/{task_id}")
            if resp.status_code == 200:
                result.update(resp.json())
        except httpx.RequestError as exc:
            log.warning("orchestrator_task_unreachable", task_id=task_id, error=str(exc))

        # Try Forgejo issue (task_id may be numeric issue number)
        try:
            issue_number = int(task_id)
            owner = config.forgejo_admin_user or "agentforge"
            repo = config.forgejo_workspace_repo
            resp = await client.get(
                f"{config.forgejo_api_base}/repos/{owner}/{repo}/issues/{issue_number}",
                headers=_forgejo_headers(),
            )
            if resp.status_code == 200:
                issue = resp.json()
                result.setdefault("title", issue.get("title"))
                result.setdefault("created_at", issue.get("created_at"))
                result["forgejo_issue"] = issue

                # Check for associated PR
                pr_resp = await client.get(
                    f"{config.forgejo_api_base}/repos/{owner}/{repo}/pulls"
                    f"?state=open&limit=50",
                    headers=_forgejo_headers(),
                )
                if pr_resp.status_code == 200:
                    for pr in pr_resp.json():
                        body = pr.get("body", "") or ""
                        if f"#{issue_number}" in body or f"issue/{issue_number}" in body:
                            result["pr"] = pr
                            result["pr_url"] = pr.get("html_url")
                            break
        except (ValueError, httpx.RequestError):
            pass

    if not result or result == {"id": task_id}:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    return result


class CreateTaskBody(BaseModel):
    title: str
    body: str = ""
    labels: list[int] = []


@router.post("/api/tasks")
async def create_task(payload: CreateTaskBody):
    """Create a new task by opening a Forgejo issue with the agent-task label."""
    owner = config.forgejo_admin_user or "agentforge"
    repo = config.forgejo_workspace_repo

    async with httpx.AsyncClient(timeout=10) as client:
        # Ensure agent-task label exists; create it if not
        label_id: Optional[int] = None
        try:
            labels_resp = await client.get(
                f"{config.forgejo_api_base}/repos/{owner}/{repo}/labels",
                headers=_forgejo_headers(),
            )
            if labels_resp.status_code == 200:
                for lbl in labels_resp.json():
                    if lbl.get("name") == AGENT_TASK_LABEL:
                        label_id = lbl["id"]
                        break

            if label_id is None:
                create_resp = await client.post(
                    f"{config.forgejo_api_base}/repos/{owner}/{repo}/labels",
                    headers=_forgejo_headers(),
                    json={"name": AGENT_TASK_LABEL, "color": "#0075ca"},
                )
                if create_resp.status_code in (200, 201):
                    label_id = create_resp.json().get("id")
        except httpx.RequestError as exc:
            log.warning("forgejo_label_check_failed", error=str(exc))

        label_ids = list(payload.labels)
        if label_id and label_id not in label_ids:
            label_ids.append(label_id)

        try:
            resp = await client.post(
                f"{config.forgejo_api_base}/repos/{owner}/{repo}/issues",
                headers=_forgejo_headers(),
                json={
                    "title": payload.title,
                    "body": payload.body,
                    "labels": label_ids,
                },
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Forgejo unreachable: {exc}") from exc

    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Forgejo error: {resp.text[:300]}",
        )

    return resp.json()


# ---------------------------------------------------------------------------
# WebSocket log streaming
# ---------------------------------------------------------------------------


@router.websocket("/ws/tasks/{task_id}/logs")
async def task_logs_ws(websocket: WebSocket, task_id: str):
    """
    Stream task logs from the orchestrator WebSocket.
    Falls back to polling GET /tasks/{task_id}/logs if WebSocket is not available.
    Each message sent to the client is JSON: {timestamp, level, message, agent}
    """
    await websocket.accept()

    orchestrator_ws_url = (
        config.orchestrator_url.replace("http://", "ws://").replace("https://", "wss://")
        + f"/ws/tasks/{task_id}/logs"
    )

    try:
        import websockets as ws_lib  # type: ignore

        async with ws_lib.connect(orchestrator_ws_url, ping_interval=20) as orch_ws:
            while True:
                try:
                    raw = await asyncio.wait_for(orch_ws.recv(), timeout=30)
                except asyncio.TimeoutError:
                    # Send a keepalive ping to the frontend
                    await websocket.send_json(
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "level": "debug",
                            "message": "keepalive",
                            "agent": None,
                        }
                    )
                    continue

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "level": "info",
                        "message": raw,
                        "agent": None,
                    }
                await websocket.send_json(data)
    except Exception as exc:  # noqa: BLE001
        log.warning("orchestrator_ws_failed_fallback_polling", task_id=task_id, error=str(exc))

        # Fallback: poll orchestrator HTTP endpoint
        seen_count = 0
        while True:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(
                        f"{config.orchestrator_url}/tasks/{task_id}/logs"
                    )
                if resp.status_code == 200:
                    lines = resp.json()
                    if isinstance(lines, list):
                        for entry in lines[seen_count:]:
                            await websocket.send_json(entry)
                        seen_count = len(lines)
                elif resp.status_code == 404:
                    await websocket.send_json(
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "level": "error",
                            "message": f"Task {task_id} not found",
                            "agent": None,
                        }
                    )
                    break
            except (httpx.RequestError, WebSocketDisconnect):
                break
            except Exception as poll_exc:  # noqa: BLE001
                log.error("log_poll_error", error=str(poll_exc))
                break
            await asyncio.sleep(2)
    finally:
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
