"""
task_monitor.py — Monitoring du fichier sentinel et état des tâches
- Poll /workspace/.task_done pour détecter la fin d'une tâche
- Lit /workspace/.task_result.json pour le résultat
- Gère le timeout
- Maintient un registre en mémoire des tâches actives
"""

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger()

# Fichiers sentinels dans le workspace
SENTINEL_FILE = ".task_done"
RESULT_FILE = ".task_result.json"
ERROR_FILE = ".task_error.json"

# Intervalle de polling en secondes
POLL_INTERVAL = 5


class TaskMonitor:
    def __init__(self, config):
        self.config = config
        self._tasks: Dict[str, Dict[str, Any]] = {}

    def register_task(self, task_id: str, info: Dict[str, Any]) -> None:
        """Enregistrer une nouvelle tâche."""
        self._tasks[task_id] = {
            **info,
            "started_at": time.time(),
            "updated_at": time.time(),
        }
        logger.info("task_registered", task_id=task_id)

    def update_task(self, task_id: str, updates: Dict[str, Any]) -> None:
        """Mettre à jour les infos d'une tâche."""
        if task_id in self._tasks:
            self._tasks[task_id].update(updates)
            self._tasks[task_id]["updated_at"] = time.time()

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Récupérer les infos d'une tâche."""
        return self._tasks.get(task_id)

    def get_active_tasks(self) -> List[Dict[str, Any]]:
        """Lister toutes les tâches actives (non terminées)."""
        active = []
        for task_id, task in self._tasks.items():
            status = task.get("status", "")
            if status not in ("completed", "failed"):
                active.append({"task_id": task_id, **task})
        return active

    async def wait_for_completion(
        self,
        task_id: str,
        workspace_path: str,
        container_id: str,
        timeout: int,
    ) -> Dict[str, Any]:
        """
        Attendre la fin de la tâche en pollant le fichier sentinel.

        Le container agent écrit /workspace/.task_done quand il a terminé.
        Le résultat est dans /workspace/.task_result.json (succès)
        ou /workspace/.task_error.json (erreur).

        Retourne un dict avec :
          - success: bool
          - final_summary: str (si succès)
          - error: str (si échec)
        """
        log = logger.bind(task_id=task_id)
        sentinel_path = os.path.join(workspace_path, SENTINEL_FILE)
        result_path = os.path.join(workspace_path, RESULT_FILE)
        error_path = os.path.join(workspace_path, ERROR_FILE)

        start_time = time.time()
        log.info("monitoring_task", workspace=workspace_path, timeout=timeout)

        while True:
            elapsed = time.time() - start_time

            # Vérifier le timeout
            if elapsed >= timeout:
                log.warning("task_timeout", elapsed=elapsed, timeout=timeout)
                return {
                    "success": False,
                    "error": f"Timeout atteint après {int(elapsed)}s",
                }

            # Vérifier si le container est encore actif
            container_running = await self._is_container_running(container_id)
            if not container_running:
                # Le container s'est arrêté — vérifier si c'est normal (sentinel présent)
                if os.path.exists(sentinel_path):
                    log.info("container_exited_with_sentinel")
                    break
                else:
                    log.warning("container_exited_unexpectedly")
                    return {
                        "success": False,
                        "crash": True,
                        "error": "Container arrêté sans sentinel (crash probable)",
                    }

            # Vérifier le fichier sentinel
            if os.path.exists(sentinel_path):
                log.info("sentinel_found", elapsed=int(elapsed))
                break

            # Log de progression toutes les 60s
            if int(elapsed) % 60 == 0 and elapsed > 0:
                log.info("task_still_running", elapsed=int(elapsed), timeout=timeout)

            await asyncio.sleep(POLL_INTERVAL)

        # Lire le résultat
        return self._read_result(result_path, error_path)

    def _read_result(
        self,
        result_path: str,
        error_path: str,
    ) -> Dict[str, Any]:
        """Lire le fichier de résultat ou d'erreur."""
        # Priorité au fichier d'erreur
        if os.path.exists(error_path):
            try:
                with open(error_path, "r", encoding="utf-8") as f:
                    error_data = json.load(f)
                return {
                    "success": False,
                    "error": error_data.get("error", "Erreur inconnue"),
                    "details": error_data,
                }
            except (json.JSONDecodeError, IOError) as e:
                return {"success": False, "error": f"Erreur (fichier illisible) : {e}"}

        # Lire le fichier de succès
        if os.path.exists(result_path):
            try:
                with open(result_path, "r", encoding="utf-8") as f:
                    result_data = json.load(f)
                return {
                    "success": True,
                    "final_summary": result_data.get("final_summary", "Tâche complétée."),
                    "plan": result_data.get("plan", ""),
                    "code_changes": result_data.get("code_changes", []),
                    "test_output": result_data.get("test_output", ""),
                    "iterations": result_data.get("iterations", 0),
                    "details": result_data,
                }
            except (json.JSONDecodeError, IOError) as e:
                # Sentinel présent mais pas de fichier résultat — considérer comme succès minimal
                logger.warning("result_file_unreadable", error=str(e))
                return {
                    "success": True,
                    "final_summary": "Tâche complétée (résultat détaillé non disponible).",
                }

        # Sentinel présent mais aucun fichier résultat — succès minimal
        logger.warning("no_result_file_but_sentinel_present")
        return {
            "success": True,
            "final_summary": "Tâche complétée par les agents.",
        }

    async def _is_container_running(self, container_id: str) -> bool:
        """Vérifier si un container Docker est encore actif."""
        try:
            import docker
            client = docker.from_env()
            container = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.containers.get(container_id),
            )
            status = container.status
            return status in ("running", "restarting", "paused")
        except Exception:
            return False
