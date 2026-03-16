"""
agent_runtime/main.py — Point d'entrée du runtime agent dans la microVM

Ce script est le premier exécuté dans le Kata Container.
Il :
1. Démarre le proxy HTTP sidecar (injection de clé API + per-agent model routing)
2. Initialise un LLM par agent, chacun configuré avec :
   - Le modèle depuis AGENT_{NAME}_MODEL (fallback sur LLM_MODEL)
   - Le header X-Agent-Name injecté via default_headers / http_client
   - La base URL pointant vers le proxy local http://localhost:{PROXY_PORT}
3. Configure le tracing LangFuse
4. Lance le graphe LangGraph avec le dict de LLMs
5. Écrit le résultat dans /workspace/.task_result.json
6. Crée le fichier sentinel /workspace/.task_done
"""

import json
import os
import socket
import subprocess
import sys
import time
from typing import Any, Dict, Optional

import structlog

# Configurer le logging en premier
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ]
)
logger = structlog.get_logger()

# Agent names for which we build dedicated LLM instances
AGENT_NAMES = ["supervisor", "architect", "coder", "tester", "reviewer"]


# ---------------------------------------------------------------------------
# Proxy sidecar
# ---------------------------------------------------------------------------

def start_proxy_sidecar() -> Optional[subprocess.Popen]:
    """
    Démarrer le proxy HTTP sidecar en arrière-plan.
    Le proxy :
    - Injecte la clé API depuis /run/secrets/llm_api_key
    - Route chaque requête vers le bon upstream selon LLM_PROVIDER
    - Sélectionne le modèle per-agent via le header X-Agent-Name
    """
    proxy_script = "/app/proxy/proxy.py"
    if not os.path.exists(proxy_script):
        for candidate in ["/proxy/proxy.py", "/opt/proxy/proxy.py"]:
            if os.path.exists(candidate):
                proxy_script = candidate
                break
        else:
            logger.warning("proxy_script_not_found", tried=proxy_script)
            # Without proxy the LLM clients would have no base URL to talk to;
            # clear any stale proxy env vars so direct connections still work.
            for var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
                os.environ.pop(var, None)
            return None

    proxy_port = os.getenv("PROXY_PORT", "8877")
    allowed_hosts = os.getenv(
        "PROXY_ALLOWED_HOSTS",
        "api.anthropic.com,api.openai.com,openrouter.ai",
    )

    proc = subprocess.Popen(
        [
            sys.executable,
            proxy_script,
            "--port", proxy_port,
            "--allowed-hosts", allowed_hosts,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for the proxy to be ready (max 10 s)
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(("localhost", int(proxy_port)), timeout=1):
                logger.info("proxy_ready", port=proxy_port)
                return proc
        except (ConnectionRefusedError, OSError):
            time.sleep(0.3)

    logger.warning("proxy_not_ready_after_timeout", port=proxy_port)
    return proc


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------

def _proxy_base_url() -> str:
    """Return the local proxy base URL."""
    port = os.getenv("PROXY_PORT", "8877")
    return f"http://localhost:{port}"


def build_llm_for_agent(agent_name: str) -> Any:
    """
    Construct an LLM instance configured for the given agent.

    - Uses AGENT_{AGENT_NAME}_MODEL (uppercase) env var for the model,
      falling back to LLM_MODEL.
    - Injects X-Agent-Name header so the proxy can select the correct model.
    - Points the client's base URL to the local proxy.
    - For Ollama: bypasses the proxy and points directly to OLLAMA_BASE_URL.
    """
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    fallback_model = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
    model_env_var = f"AGENT_{agent_name.upper()}_MODEL"
    model = os.getenv(model_env_var, fallback_model)
    proxy_url = _proxy_base_url()

    logger.info(
        "building_llm_for_agent",
        agent=agent_name,
        provider=provider,
        model=model,
        proxy=proxy_url,
    )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        # Inject X-Agent-Name via default_headers so the proxy can route
        # each agent to the correct model. Using default_headers (not http_client)
        # because http_client gets forwarded to Messages.create() as a kwarg
        # in langchain_anthropic >= 0.3 and causes a TypeError.
        return ChatAnthropic(
            model=model,
            # Dummy key — the proxy replaces it with the real secret
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "proxy-injected"),
            anthropic_api_url=proxy_url,
            default_headers={"X-Agent-Name": agent_name},
            temperature=0,
            max_tokens=8192,
        )

    elif provider in ("openai", "openrouter"):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            api_key=os.getenv("OPENAI_API_KEY", os.getenv("OPENROUTER_API_KEY", "proxy-injected")),
            base_url=proxy_url,
            default_headers={"X-Agent-Name": agent_name},
            temperature=0,
            max_tokens=4096,
        )

    elif provider == "ollama":
        # Ollama runs locally; no proxy needed.
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        try:
            from langchain_ollama import ChatOllama
        except ImportError:
            from langchain_community.chat_models import ChatOllama  # type: ignore[no-redef]
        return ChatOllama(
            model=model,
            base_url=ollama_url,
            temperature=0,
        )

    else:
        raise ValueError(
            f"LLM_PROVIDER '{provider}' is not supported. "
            "Supported providers: anthropic, openai, openrouter, ollama"
        )


def build_llms() -> Dict[str, Any]:
    """
    Build a dict of per-agent LLM instances:
      {supervisor, architect, coder, tester, reviewer}

    Also stores a "default" key pointing to the supervisor LLM as a safe
    fallback for any unexpected node lookups.
    """
    llms: Dict[str, Any] = {}
    for agent_name in AGENT_NAMES:
        llms[agent_name] = build_llm_for_agent(agent_name)
    # Convenience fallback used by graph.py for unknown node names
    llms["default"] = llms["supervisor"]
    return llms


# ---------------------------------------------------------------------------
# LangFuse tracing
# ---------------------------------------------------------------------------

def setup_langfuse_tracing() -> Optional[Any]:
    """
    Configure LangFuse tracing.
    Returns the CallbackHandler, or None if LangFuse is not configured.
    """
    langfuse_host = os.getenv("LANGFUSE_HOST", "")
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")

    if not all([langfuse_host, public_key, secret_key]):
        logger.info("langfuse_tracing_disabled", reason="missing_config")
        return None

    try:
        from langfuse.langchain import CallbackHandler  # type: ignore[import-untyped]
        handler = CallbackHandler(
            host=langfuse_host,
            public_key=public_key,
            secret_key=secret_key,
        )
        logger.info("langfuse_tracing_enabled", host=langfuse_host)
        return handler
    except ImportError:
        logger.warning("langfuse_not_installed_or_incompatible_api")
        return None
    except Exception as exc:
        logger.warning("langfuse_setup_failed", error=str(exc))
        return None


# ---------------------------------------------------------------------------
# Result / error / sentinel writers
# ---------------------------------------------------------------------------

def write_result(workspace_path: str, result: dict) -> None:
    result_path = os.path.join(workspace_path, ".task_result.json")
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    logger.info("result_written", path=result_path)


def write_error(workspace_path: str, error: str) -> None:
    error_path = os.path.join(workspace_path, ".task_error.json")
    with open(error_path, "w", encoding="utf-8") as fh:
        json.dump({"error": error, "timestamp": time.time()}, fh)
    logger.error("error_written", path=error_path, error=error[:200])


def write_sentinel(workspace_path: str) -> None:
    sentinel_path = os.path.join(workspace_path, ".task_done")
    with open(sentinel_path, "w") as fh:
        fh.write(str(time.time()))
    logger.info("sentinel_written", path=sentinel_path)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Main entry point for the agent runtime."""
    start_time = time.time()

    # --- Environment variables ---
    workspace_path = os.getenv("WORKSPACE_PATH", "/workspace")
    task_id = os.getenv("TASK_ID", "unknown")
    task_description = os.getenv("TASK_DESCRIPTION", "")
    issue_number = int(os.getenv("ISSUE_NUMBER", "0"))
    branch_name = os.getenv("BRANCH_NAME", "main")
    repo_owner = os.getenv("REPO_OWNER", "admin")
    repo_name = os.getenv("REPO_NAME", "agentforge-workspace")
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    default_model = os.getenv("LLM_MODEL", "claude-sonnet-4-6")

    logger.info(
        "agent_runtime_start",
        task_id=task_id,
        workspace=workspace_path,
        issue=issue_number,
        provider=provider,
    )

    if not task_description:
        write_error(workspace_path, "TASK_DESCRIPTION is empty")
        write_sentinel(workspace_path)
        sys.exit(1)

    if not os.path.isdir(workspace_path):
        write_error(workspace_path, f"Workspace not found: {workspace_path}")
        write_sentinel(workspace_path)
        sys.exit(1)

    # --- 1. Start proxy sidecar ---
    proxy_proc = start_proxy_sidecar()

    # --- 2. Build per-agent LLMs ---
    try:
        llms = build_llms()
    except Exception as exc:
        logger.error("llm_build_failed", error=str(exc))
        write_error(workspace_path, f"Failed to initialise LLMs: {exc}")
        write_sentinel(workspace_path)
        if proxy_proc:
            proxy_proc.terminate()
        sys.exit(1)

    # --- 3. Configure LangFuse ---
    langfuse_handler = setup_langfuse_tracing()
    langfuse_callbacks = [langfuse_handler] if langfuse_handler else []

    # --- 4. Build initial state ---
    # Collect the actual model names resolved for each agent for tracing purposes
    agent_models: Dict[str, str] = {}
    for name in AGENT_NAMES:
        env_key = f"AGENT_{name.upper()}_MODEL"
        agent_models[name] = os.getenv(env_key, default_model)

    from state import TaskState  # local import; state.py lives in same package
    initial_state: TaskState = {
        "task_description": task_description,
        "repo_path": workspace_path,
        "issue_number": issue_number,
        "branch_name": branch_name,
        "repo_owner": repo_owner,
        "repo_name": repo_name,
        "plan": "",
        "is_micro_task": False,
        "code_changes": [],
        "commit_messages": [],
        "test_output": "",
        "tests_passed": False,
        "review_feedback": "",
        "review_approved": False,
        "iterations": 0,
        "done": False,
        "final_summary": "",
        "error": None,
        # Agent model info for tracing / observability
        "agent_models": agent_models,
        "llm_provider": provider,
    }

    # --- 5. Run LangGraph ---
    try:
        from graph import run_graph  # local import

        thread_id = f"task-{task_id}"
        resume = os.environ.get("RESUME", "").lower() == "true"
        if resume:
            logger.info("resuming_from_checkpoint", task_id=task_id, thread_id=thread_id)
        final_state = run_graph(
            llms=llms,
            initial_state=initial_state,
            workspace_path=workspace_path,
            thread_id=thread_id,
            resume=resume,
        )

        elapsed = time.time() - start_time
        logger.info(
            "pipeline_complete",
            elapsed=f"{elapsed:.1f}s",
            approved=final_state.get("review_approved"),
            iterations=final_state.get("iterations"),
            changes=len(final_state.get("code_changes", [])),
        )

        result = {
            "success": True,
            "task_id": task_id,
            "issue_number": issue_number,
            "final_summary": final_state.get("final_summary", ""),
            "plan": final_state.get("plan", ""),
            "code_changes": final_state.get("code_changes", []),
            "commit_messages": final_state.get("commit_messages", []),
            "test_output": final_state.get("test_output", "")[:2000],
            "tests_passed": final_state.get("tests_passed", False),
            "review_feedback": final_state.get("review_feedback", "")[:1000],
            "iterations": final_state.get("iterations", 0),
            "elapsed_seconds": round(elapsed, 1),
            "agent_models": agent_models,
            "llm_provider": provider,
        }
        write_result(workspace_path, result)

    except Exception as exc:
        logger.error("pipeline_failed", error=str(exc), exc_info=True)
        write_error(workspace_path, str(exc))

    finally:
        # --- 6. Always write sentinel ---
        write_sentinel(workspace_path)

        if proxy_proc:
            proxy_proc.terminate()

        logger.info("agent_runtime_exit")


if __name__ == "__main__":
    main()
