"""
Chat agent route – "Chef de projet" assistant.

POST /api/chat
  Body : {message, conversation_id?, conversation_history?: [{role, content}]}
  Response: text/event-stream SSE

The agent:
  1. Receives a user message.
  2. Decides whether to call a tool (via LLM function calling).
  3. Executes tool (orchestrator/Forgejo HTTP calls only).
  4. Streams the final text response back as SSE.

Supports Anthropic, OpenAI, and OpenRouter providers.
"""
from __future__ import annotations

import json
import uuid
from typing import AsyncIterator, Optional

import httpx
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from config import config

log = structlog.get_logger(__name__)
router = APIRouter(tags=["chat"])

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI-style – works for Anthropic + OpenAI + OpenRouter)
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_system_status",
            "description": "Get the current health status of all services (Forgejo, LangFuse, Orchestrator).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List all current agent tasks (active and historical).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a new agent task by opening a Forgejo issue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Issue title"},
                    "body": {"type": "string", "description": "Detailed task description"},
                },
                "required": ["title", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task_status",
            "description": "Get detailed status of a specific task by its issue number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "issue_number": {"type": "integer", "description": "The Forgejo issue number"}
                },
                "required": ["issue_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_prs",
            "description": "List open pull requests in the workspace repository.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

SYSTEM_PROMPT = (
    "You are the Chef de projet assistant for AgentForge, an AI-powered multi-agent software "
    "development platform. You help users monitor agent activity, understand task status, and "
    "formulate new tasks for the agents.\n\n"
    "You have access to tools to check system status, list tasks, create tasks, and list pull "
    "requests. Use them proactively when the user's question would benefit from live data.\n\n"
    "When creating tasks, write clear, detailed descriptions so the agents understand exactly "
    "what to implement. Always confirm task creation with the user before calling create_task."
)


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


async def _execute_tool(name: str, args: dict) -> str:
    """Execute a tool call and return the result as a JSON string."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if name == "get_system_status":
                resp = await client.get(f"http://localhost:3020/api/system/status")
                return resp.text

            elif name == "list_tasks":
                resp = await client.get(f"http://localhost:3020/api/tasks")
                return resp.text

            elif name == "create_task":
                title = args.get("title", "")
                body = args.get("body", "")
                if not title:
                    return json.dumps({"error": "title is required"})
                owner = config.forgejo_admin_user or "agentforge"
                repo = config.forgejo_workspace_repo
                headers = {**config.forgejo_auth_headers(), "Content-Type": "application/json"}
                resp = await client.post(
                    f"{config.forgejo_api_base}/repos/{owner}/{repo}/issues",
                    headers=headers,
                    json={"title": title, "body": body},
                )
                return resp.text

            elif name == "get_task_status":
                issue_number = args.get("issue_number")
                if issue_number is None:
                    return json.dumps({"error": "issue_number is required"})
                resp = await client.get(f"http://localhost:3020/api/tasks/{issue_number}")
                return resp.text

            elif name == "list_prs":
                resp = await client.get(f"http://localhost:3020/api/prs")
                return resp.text

            else:
                return json.dumps({"error": f"Unknown tool: {name}"})

    except Exception as exc:  # noqa: BLE001
        log.error("tool_execution_error", tool=name, error=str(exc))
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# LLM provider adapters
# ---------------------------------------------------------------------------


def _pick_model() -> str:
    """Choose the cheapest model: supervisor model, then general LLM model."""
    return config.agent_supervisor_model or config.llm_model or ""


def _anthropic_messages(
    history: list[dict],
    user_message: str,
) -> tuple[str, list[dict]]:
    """Build Anthropic-style messages list. Returns (system_prompt, messages)."""
    messages: list[dict] = []
    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("user", "assistant"):
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return SYSTEM_PROMPT, messages


def _openai_messages(history: list[dict], user_message: str) -> list[dict]:
    """Build OpenAI-style messages list (system included)."""
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("user", "assistant"):
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return messages


# ---------------------------------------------------------------------------
# Core agentic loop (non-streaming for tool calls, then stream final answer)
# ---------------------------------------------------------------------------


async def _anthropic_agent_stream(
    message: str,
    history: list[dict],
) -> AsyncIterator[str]:
    """
    Run agentic loop with Anthropic API.
    Yields SSE data strings.
    """
    api_key = config.anthropic_api_key
    if not api_key:
        yield _sse_error("ANTHROPIC_API_KEY is not configured")
        return

    model = _pick_model()
    if not model:
        yield _sse_error("No LLM model configured")
        return

    # Convert tools to Anthropic format
    anthropic_tools = [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "input_schema": t["function"]["parameters"],
        }
        for t in TOOLS
    ]

    system_prompt, messages = _anthropic_messages(history, message)

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        # Agentic loop (up to 5 tool call rounds)
        for _round in range(5):
            payload = {
                "model": model,
                "max_tokens": 1024,
                "system": system_prompt,
                "messages": messages,
                "tools": anthropic_tools,
            }
            try:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json=payload,
                )
            except httpx.RequestError as exc:
                yield _sse_error(f"Anthropic API unreachable: {exc}")
                return

            if resp.status_code != 200:
                yield _sse_error(f"Anthropic API error {resp.status_code}: {resp.text[:200]}")
                return

            data = resp.json()
            stop_reason = data.get("stop_reason")
            content_blocks = data.get("content", [])

            # Collect text and tool_use blocks
            text_parts: list[str] = []
            tool_uses: list[dict] = []
            for block in content_blocks:
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    tool_uses.append(block)

            if stop_reason == "tool_use" and tool_uses:
                # Announce tool calls
                for tu in tool_uses:
                    yield _sse_action(f"Calling tool: {tu['name']}")

                # Build assistant message with all content blocks
                messages.append({"role": "assistant", "content": content_blocks})

                # Execute all tool calls and build tool_result blocks
                tool_result_content: list[dict] = []
                for tu in tool_uses:
                    result = await _execute_tool(tu["name"], tu.get("input", {}))
                    tool_result_content.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu["id"],
                            "content": result,
                        }
                    )
                messages.append({"role": "user", "content": tool_result_content})
                continue

            # Final answer – stream the text
            full_text = "".join(text_parts)
            if full_text:
                # Stream word by word for a nicer UX
                words = full_text.split(" ")
                for i, word in enumerate(words):
                    chunk = word if i == len(words) - 1 else word + " "
                    yield _sse_text(chunk)
            break

    yield _sse_done()


async def _openai_agent_stream(
    message: str,
    history: list[dict],
    base_url: str = "https://api.openai.com/v1",
    api_key_override: Optional[str] = None,
) -> AsyncIterator[str]:
    """
    Run agentic loop with OpenAI-compatible API (also used for OpenRouter).
    Yields SSE data strings.
    """
    provider = config.llm_provider.lower() if config.llm_provider else ""
    if api_key_override:
        api_key = api_key_override
    elif provider == "openai":
        api_key = config.openai_api_key
    else:
        api_key = config.openrouter_api_key

    if not api_key:
        yield _sse_error("LLM API key is not configured")
        return

    model = _pick_model()
    if not model:
        yield _sse_error("No LLM model configured")
        return

    messages = _openai_messages(history, message)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if "openrouter" in base_url:
        headers["HTTP-Referer"] = "https://agentforge.local"
        headers["X-Title"] = "AgentForge Dashboard"

    async with httpx.AsyncClient(timeout=60) as client:
        for _round in range(5):
            payload = {
                "model": model,
                "messages": messages,
                "tools": TOOLS,
                "tool_choice": "auto",
                "max_tokens": 1024,
            }
            try:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
            except httpx.RequestError as exc:
                yield _sse_error(f"LLM API unreachable: {exc}")
                return

            if resp.status_code != 200:
                yield _sse_error(f"LLM API error {resp.status_code}: {resp.text[:200]}")
                return

            data = resp.json()
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            finish_reason = choice.get("finish_reason")
            tool_calls = msg.get("tool_calls") or []

            if finish_reason == "tool_calls" and tool_calls:
                # Announce
                for tc in tool_calls:
                    yield _sse_action(f"Calling tool: {tc['function']['name']}")

                messages.append({"role": "assistant", "tool_calls": tool_calls, "content": msg.get("content") or ""})

                for tc in tool_calls:
                    fn = tc["function"]
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        args = {}
                    result = await _execute_tool(fn["name"], args)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        }
                    )
                continue

            # Final text
            content = msg.get("content") or ""
            if content:
                words = content.split(" ")
                for i, word in enumerate(words):
                    chunk = word if i == len(words) - 1 else word + " "
                    yield _sse_text(chunk)
            break

    yield _sse_done()


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


def _sse_text(content: str) -> str:
    return json.dumps({"type": "text", "content": content})


def _sse_action(content: str) -> str:
    return json.dumps({"type": "action", "content": content})


def _sse_done() -> str:
    return json.dumps({"type": "done", "content": ""})


def _sse_error(content: str) -> str:
    return json.dumps({"type": "error", "content": content})


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class ConversationMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    conversation_history: list[ConversationMessage] = []


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/api/chat")
async def chat(payload: ChatRequest):
    """
    SSE streaming chat endpoint.
    Streams: data: {type: "text"|"action"|"done"|"error", content: "..."}
    """
    if not config.llm_provider:
        raise HTTPException(status_code=503, detail="LLM provider is not configured")

    history = [{"role": m.role, "content": m.content} for m in payload.conversation_history]
    provider = config.llm_provider.lower()

    if provider == "anthropic":
        gen = _anthropic_agent_stream(payload.message, history)
    elif provider == "openai":
        gen = _openai_agent_stream(payload.message, history, base_url="https://api.openai.com/v1")
    elif provider == "openrouter":
        gen = _openai_agent_stream(
            payload.message, history, base_url="https://openrouter.ai/api/v1"
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported LLM provider: {config.llm_provider}")

    async def event_generator():
        async for data in gen:
            yield {"data": data}

    return EventSourceResponse(event_generator())
