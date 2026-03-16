"""
agent_runtime/main.py — Point d'entrée du runtime agent dans la microVM

Ce script est le premier exécuté dans le Kata Container.
Il :
1. Démarre le proxy HTTP sidecar (injection de clé API)
2. Initialise le LLM selon le provider configuré
3. Configure le tracing LangFuse
4. Lance le graphe LangGraph
5. Écrit le résultat dans /workspace/.task_result.json
6. Crée le fichier sentinel /workspace/.task_done
"""

import json
import os
import subprocess
import sys
import time

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


def start_proxy_sidecar() -> subprocess.Popen:
    """
    Démarrer le proxy HTTP sidecar en arrière-plan.
    Le proxy lit la clé API depuis /run/secrets/llm_api_key
    et l'injecte dans les requêtes vers api.anthropic.com / api.openai.com.
    """
    proxy_script = "/app/proxy/proxy.py"
    if not os.path.exists(proxy_script):
        # Chercher dans d'autres emplacements
        for path in ["/proxy/proxy.py", "/opt/proxy/proxy.py"]:
            if os.path.exists(path):
                proxy_script = path
                break
        else:
            logger.warning("proxy_script_not_found", tried=[proxy_script])
            return None

    proxy_port = os.getenv("PROXY_PORT", "8877")
    allowed_hosts = os.getenv("PROXY_ALLOWED_HOSTS", "api.anthropic.com,api.openai.com")

    proc = subprocess.Popen(
        [sys.executable, proxy_script, "--port", proxy_port, "--allowed-hosts", allowed_hosts],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Attendre que le proxy soit prêt (max 10s)
    import socket
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(("localhost", int(proxy_port)), timeout=1):
                logger.info("proxy_ready", port=proxy_port)
                return proc
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)

    logger.warning("proxy_not_ready", port=proxy_port)
    return proc


def build_llm():
    """
    Construire l'instance LLM selon la configuration.
    Le provider est défini par la variable LLM_PROVIDER.
    La clé API est récupérée via le proxy (jamais directement).
    """
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    model = os.getenv("LLM_MODEL", "claude-sonnet-4-6")

    logger.info("building_llm", provider=provider, model=model)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        # La vraie clé API est injectée par le proxy.
        # On passe une clé factice ici — elle sera remplacée par le proxy.
        # En mode proxy, HTTPS_PROXY redirige tout vers le sidecar qui injecte la vraie clé.
        api_key = os.getenv("ANTHROPIC_API_KEY", "proxy-injected")
        return ChatAnthropic(
            model=model,
            anthropic_api_key=api_key,
            temperature=0,
            max_tokens=8192,
        )

    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        api_key = os.getenv("OPENAI_API_KEY", "proxy-injected")
        return ChatOpenAI(
            model=model,
            api_key=api_key,
            temperature=0,
            max_tokens=4096,
        )

    elif provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError:
            from langchain_community.chat_models import ChatOllama
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        return ChatOllama(
            model=model,
            base_url=ollama_url,
            temperature=0,
        )

    else:
        raise ValueError(f"LLM_PROVIDER '{provider}' non supporté. Options : anthropic, openai, ollama")


def setup_langfuse_tracing():
    """
    Configurer le tracing LangFuse.
    Retourne le CallbackHandler ou None si LangFuse n'est pas configuré.
    """
    langfuse_host = os.getenv("LANGFUSE_HOST", "")
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")

    if not all([langfuse_host, public_key, secret_key]):
        logger.info("langfuse_tracing_disabled", reason="missing config")
        return None

    try:
        from langfuse.callback import CallbackHandler
        handler = CallbackHandler(
            host=langfuse_host,
            public_key=public_key,
            secret_key=secret_key,
        )
        logger.info("langfuse_tracing_enabled", host=langfuse_host)
        return handler
    except ImportError:
        logger.warning("langfuse_not_installed")
        return None
    except Exception as e:
        logger.warning("langfuse_setup_failed", error=str(e))
        return None


def write_result(workspace_path: str, result: dict) -> None:
    """Écrire le fichier de résultat."""
    result_path = os.path.join(workspace_path, ".task_result.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info("result_written", path=result_path)


def write_error(workspace_path: str, error: str) -> None:
    """Écrire le fichier d'erreur."""
    error_path = os.path.join(workspace_path, ".task_error.json")
    with open(error_path, "w", encoding="utf-8") as f:
        json.dump({"error": error, "timestamp": time.time()}, f)
    logger.error("error_written", path=error_path, error=error[:200])


def write_sentinel(workspace_path: str) -> None:
    """Écrire le fichier sentinel signalant la fin de la tâche."""
    sentinel_path = os.path.join(workspace_path, ".task_done")
    with open(sentinel_path, "w") as f:
        f.write(str(time.time()))
    logger.info("sentinel_written", path=sentinel_path)


def main():
    """Point d'entrée principal du runtime agent."""
    start_time = time.time()

    # --- Variables d'environnement ---
    workspace_path = os.getenv("WORKSPACE_PATH", "/workspace")
    task_id = os.getenv("TASK_ID", "unknown")
    task_description = os.getenv("TASK_DESCRIPTION", "")
    issue_number = int(os.getenv("ISSUE_NUMBER", "0"))
    branch_name = os.getenv("BRANCH_NAME", "main")
    repo_owner = os.getenv("REPO_OWNER", "admin")
    repo_name = os.getenv("REPO_NAME", "agentforge-workspace")

    logger.info(
        "agent_runtime_start",
        task_id=task_id,
        workspace=workspace_path,
        issue=issue_number,
    )

    if not task_description:
        write_error(workspace_path, "TASK_DESCRIPTION est vide")
        write_sentinel(workspace_path)
        sys.exit(1)

    if not os.path.isdir(workspace_path):
        write_error(workspace_path, f"Workspace introuvable : {workspace_path}")
        write_sentinel(workspace_path)
        sys.exit(1)

    # --- 1. Démarrer le proxy sidecar ---
    proxy_proc = start_proxy_sidecar()

    # --- 2. Construire le LLM ---
    try:
        llm = build_llm()
    except Exception as e:
        logger.error("llm_build_failed", error=str(e))
        write_error(workspace_path, f"Impossible d'initialiser le LLM : {e}")
        write_sentinel(workspace_path)
        sys.exit(1)

    # --- 3. Configurer LangFuse ---
    langfuse_handler = setup_langfuse_tracing()
    if langfuse_handler:
        # Wrapper le LLM avec le callback LangFuse
        from langchain_core.callbacks import CallbackManager
        # LangGraph prend les callbacks en config
        langfuse_callbacks = [langfuse_handler]
    else:
        langfuse_callbacks = []

    # --- 4. Construire l'état initial ---
    from state import TaskState
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
    }

    # --- 5. Lancer le graphe LangGraph ---
    try:
        from graph import run_graph

        thread_id = f"task-{task_id}"
        final_state = run_graph(
            llm=llm,
            initial_state=initial_state,
            workspace_path=workspace_path,
            thread_id=thread_id,
        )

        elapsed = time.time() - start_time
        logger.info(
            "pipeline_complete",
            elapsed=f"{elapsed:.1f}s",
            approved=final_state.get("review_approved"),
            iterations=final_state.get("iterations"),
            changes=len(final_state.get("code_changes", [])),
        )

        # Écrire le résultat
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
        }
        write_result(workspace_path, result)

    except Exception as e:
        logger.error("pipeline_failed", error=str(e), exc_info=True)
        write_error(workspace_path, str(e))

    finally:
        # --- 6. Toujours écrire le sentinel ---
        write_sentinel(workspace_path)

        # Arrêter le proxy
        if proxy_proc:
            proxy_proc.terminate()

        logger.info("agent_runtime_exit")


if __name__ == "__main__":
    main()
