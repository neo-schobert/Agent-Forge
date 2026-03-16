"""
container_manager.py — Gestion du cycle de vie des containers Kata
- Spawn une microVM (Kata Container) par tâche
- Monte le workspace en bind mount
- Injecte les variables d'environnement (sans API keys)
- Détruit le container après usage
"""

import asyncio
import json
import os
import time
from typing import Dict, Optional

import docker
import structlog

logger = structlog.get_logger()

# Réseau Docker interne utilisé pour les containers agents
AGENT_NETWORK = "agentforge_net"


class ContainerManager:
    def __init__(self, config):
        self.config = config
        self._docker: Optional[docker.DockerClient] = None
        self._active_containers: Dict[str, str] = {}  # task_id → container_id

    @property
    def docker_client(self) -> docker.DockerClient:
        if self._docker is None:
            self._docker = docker.from_env()
        return self._docker

    async def spawn(
        self,
        task_id: str,
        workspace_path: str,
        task_description: str,
        issue_number: int,
        branch_name: str,
        repo_owner: str,
        repo_name: str,
    ) -> str:
        """
        Spawner un container agent pour traiter la tâche.
        Retourne l'ID du container.
        """
        log = logger.bind(task_id=task_id)
        log.info("spawning_container", workspace=workspace_path)

        # Choisir le runtime : kata-qemu si disponible, sinon runc
        runtime = self._get_runtime()
        log.info("using_runtime", runtime=runtime)

        # Variables d'environnement pour l'agent runtime
        # IMPORTANT : pas de clé API ici — elles passent via le proxy
        env_vars = {
            "TASK_ID": task_id,
            "TASK_DESCRIPTION": task_description,
            "ISSUE_NUMBER": str(issue_number),
            "BRANCH_NAME": branch_name,
            "REPO_OWNER": repo_owner,
            "REPO_NAME": repo_name,
            "FORGEJO_BASE_URL": self.config.FORGEJO_BASE_URL,
            "FORGEJO_ADMIN_USER": self.config.FORGEJO_ADMIN_USER,
            # Token Git pour les commits (différent de la clé LLM)
            "FORGEJO_API_TOKEN": self.config.FORGEJO_API_TOKEN,
            # Proxy sidecar : toutes les requêtes LLM passent par là
            "HTTPS_PROXY": f"http://localhost:{self.config.PROXY_PORT}",
            "HTTP_PROXY": f"http://localhost:{self.config.PROXY_PORT}",
            "https_proxy": f"http://localhost:{self.config.PROXY_PORT}",
            "http_proxy": f"http://localhost:{self.config.PROXY_PORT}",
            # Config LLM (provider + modèle seulement — PAS la clé)
            "LLM_PROVIDER": os.getenv("LLM_PROVIDER", "anthropic"),
            "LLM_MODEL": os.getenv("LLM_MODEL", "claude-sonnet-4-6"),
            "OLLAMA_BASE_URL": os.getenv("OLLAMA_BASE_URL", "http://host-gateway:11434"),
            # LangFuse tracing
            "LANGFUSE_HOST": self.config.LANGFUSE_HOST,
            "LANGFUSE_PUBLIC_KEY": self.config.LANGFUSE_PUBLIC_KEY,
            "LANGFUSE_SECRET_KEY": self.config.LANGFUSE_SECRET_KEY_API,
            # Chemins dans la microVM
            "WORKSPACE_PATH": "/workspace",
            "SENTINEL_FILE": "/workspace/.task_done",
            "RESULT_FILE": "/workspace/.task_result.json",
            "ERROR_FILE": "/workspace/.task_error.json",
        }

        # Volumes montés dans la microVM
        # IMPORTANT : les paths doivent être des paths HOST (pas container-internal)
        # puisque Docker daemon résout les paths côté host
        secrets_host_path = os.getenv("SECRETS_HOST_PATH", "/run/secrets")
        volumes = {
            workspace_path: {"bind": "/workspace", "mode": "rw"},
        }
        # Monter les secrets seulement si le répertoire existe sur le host
        if os.path.isdir(secrets_host_path):
            volumes[secrets_host_path] = {"bind": "/run/secrets", "mode": "ro"}

        # Limits de ressources
        mem_limit = f"{self.config.KATA_RAM_MB}m"
        nano_cpus = self.config.KATA_VCPUS * 1_000_000_000

        container_name = f"agentforge_task_{task_id}"

        try:
            # Lancer le proxy sidecar EN PREMIER dans le même container
            # (En pratique, on lance agent_runtime qui démarre le proxy en interne)
            container = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.docker_client.containers.run(
                    image=self.config.AGENT_IMAGE,
                    name=container_name,
                    runtime=runtime,
                    environment=env_vars,
                    volumes=volumes,
                    mem_limit=mem_limit,
                    nano_cpus=nano_cpus,
                    network=AGENT_NETWORK,
                    detach=True,
                    auto_remove=False,
                    labels={
                        "com.agentforge.task_id": task_id,
                        "com.agentforge.issue": str(issue_number),
                        "com.docker.compose.project": "agentforge",
                    },
                    # Pas de privilèges, isolation maximale
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges:true"],
                ),
            )
        except docker.errors.APIError as e:
            # Fallback sur runc si kata échoue (ex: VPS sans KVM)
            if runtime != "runc" and "kata" in str(e).lower():
                logger.warning(
                    "kata_fallback_runc",
                    error=str(e)[:200],
                    task_id=task_id,
                )
                container = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.docker_client.containers.run(
                        image=self.config.AGENT_IMAGE,
                        name=container_name,
                        environment=env_vars,
                        volumes=volumes,
                        mem_limit=mem_limit,
                        nano_cpus=nano_cpus,
                        network=AGENT_NETWORK,
                        detach=True,
                        auto_remove=False,
                        labels={
                            "com.agentforge.task_id": task_id,
                            "com.agentforge.issue": str(issue_number),
                        },
                    ),
                )
            else:
                raise

        container_id = container.id
        self._active_containers[task_id] = container_id
        log.info("container_started", container_id=container_id[:12])

        return container_id

    async def destroy(self, container_id: str) -> None:
        """Arrêter et supprimer un container."""
        try:
            container = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.docker_client.containers.get(container_id),
            )
            # Stop gracieux puis force kill
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: container.stop(timeout=10),
            )
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: container.remove(force=True),
            )
            logger.info("container_destroyed", container_id=container_id[:12])
        except docker.errors.NotFound:
            logger.debug("container_already_gone", container_id=container_id[:12])
        except Exception as e:
            logger.warning("container_destroy_failed", container_id=container_id[:12], error=str(e))

        # Retirer de la liste active
        for task_id, cid in list(self._active_containers.items()):
            if cid == container_id:
                del self._active_containers[task_id]

    async def cleanup_all(self) -> None:
        """Nettoyer tous les containers actifs (appelé au shutdown)."""
        logger.info("cleanup_all_containers", count=len(self._active_containers))
        for task_id, container_id in list(self._active_containers.items()):
            await self.destroy(container_id)

    async def get_logs(self, container_id: str, tail: int = 100) -> str:
        """Récupérer les logs d'un container."""
        try:
            container = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.docker_client.containers.get(container_id),
            )
            logs = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: container.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace"),
            )
            return logs
        except docker.errors.NotFound:
            return "(container introuvable)"
        except Exception as e:
            return f"(erreur récupération logs : {e})"

    def _get_runtime(self) -> str:
        """Déterminer le runtime Docker à utiliser."""
        if not self.config.KATA_AVAILABLE:
            return "runc"

        # Vérifier si kata-qemu est disponible dans Docker
        try:
            info = self.docker_client.info()
            runtimes = info.get("Runtimes", {})
            if "kata-qemu" in runtimes:
                return "kata-qemu"
            if "kata-runtime" in runtimes:
                return "kata-runtime"
        except Exception:
            pass

        logger.warning("kata_runtime_not_in_docker", fallback="runc")
        return "runc"
