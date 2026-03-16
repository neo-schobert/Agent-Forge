"""
webhook_handler.py — Traitement des webhooks Forgejo
- Vérifie la signature HMAC
- Extrait la description de la tâche depuis l'issue
- Lance le pipeline agent en arrière-plan
"""

import asyncio
import hashlib
import hmac
import json
import re
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger()

# Label qui déclenche le pipeline
AGENT_TASK_LABEL = "agent-task"


class WebhookHandler:
    def __init__(self, config, git_manager, container_manager, task_monitor):
        self.config = config
        self.git_manager = git_manager
        self.container_manager = container_manager
        self.task_monitor = task_monitor

    async def handle(self, body: bytes, headers: Dict[str, str]) -> Dict[str, Any]:
        """
        Traiter un webhook entrant de Forgejo.
        Retourne un dict avec le résultat (accepté ou ignoré).
        """
        # 1. Vérification signature HMAC (si secret configuré)
        if self.config.FORGEJO_WEBHOOK_SECRET:
            self._verify_signature(body, headers)

        # 2. Parser le payload JSON
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"Payload JSON invalide : {e}")

        # 3. Déterminer le type d'événement
        event_type = headers.get("x-forgejo-event", headers.get("x-gitea-event", ""))
        logger.info("webhook_received", event_type=event_type)

        # 4. Filtrer les événements pertinents
        if event_type not in ("issues", "issue"):
            logger.debug("webhook_ignored", event_type=event_type, reason="not an issue event")
            return {"status": "ignored", "reason": f"event type '{event_type}' not handled"}

        # 5. Vérifier l'action (on traite seulement "opened" et "labeled")
        action = payload.get("action", "")
        if action not in ("opened", "labeled"):
            logger.debug("webhook_ignored", action=action)
            return {"status": "ignored", "reason": f"action '{action}' not handled"}

        # 6. Extraire l'issue
        issue = payload.get("issue", {})
        if not issue:
            return {"status": "ignored", "reason": "no issue in payload"}

        # 7. Vérifier que le label "agent-task" est présent
        labels = issue.get("labels", [])
        label_names = [lbl.get("name", "") for lbl in labels]

        if AGENT_TASK_LABEL not in label_names:
            logger.debug("webhook_ignored", labels=label_names, reason="no agent-task label")
            return {"status": "ignored", "reason": "label 'agent-task' not present"}

        # 8. Extraire les informations de la tâche
        issue_number = issue.get("number")
        issue_title = issue.get("title", f"task-{issue_number}")
        issue_body = issue.get("body", "")
        repo = payload.get("repository", {})
        repo_name = repo.get("name", self.config.FORGEJO_WORKSPACE_REPO)
        repo_owner = repo.get("owner", {}).get("login", self.config.FORGEJO_ADMIN_USER)

        if not issue_number:
            raise ValueError("Impossible d'extraire le numéro de l'issue")

        # 9. Construire la description complète de la tâche
        task_description = self._build_task_description(issue_title, issue_body)

        # 10. Générer le nom de branche
        branch_name = self._make_branch_name(issue_number, issue_title)

        logger.info(
            "task_accepted",
            issue=issue_number,
            title=issue_title,
            branch=branch_name,
            repo=f"{repo_owner}/{repo_name}",
        )

        # 11. Lancer le pipeline en arrière-plan (non-bloquant)
        asyncio.create_task(
            self._run_pipeline(
                issue_number=issue_number,
                issue_title=issue_title,
                task_description=task_description,
                branch_name=branch_name,
                repo_owner=repo_owner,
                repo_name=repo_name,
            )
        )

        return {
            "status": "accepted",
            "issue": issue_number,
            "branch": branch_name,
            "message": "Pipeline agent démarré",
        }

    def _verify_signature(self, body: bytes, headers: Dict[str, str]) -> None:
        """Vérifier la signature HMAC-SHA256 du webhook Forgejo."""
        # Forgejo envoie le header X-Forgejo-Signature ou X-Gitea-Signature
        signature_header = (
            headers.get("x-forgejo-signature-256", "")
            or headers.get("x-gitea-signature", "")
            or headers.get("x-hub-signature-256", "")
        )

        if not signature_header:
            # Si pas de signature dans les headers, vérifier si c'est attendu
            logger.warning("webhook_no_signature")
            return  # On est permissif si pas de header (utile pour les tests)

        # Calculer la signature attendue
        expected = hmac.new(
            self.config.FORGEJO_WEBHOOK_SECRET.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        # Forgejo préfixe parfois avec "sha256="
        received = signature_header.replace("sha256=", "")

        if not hmac.compare_digest(expected, received):
            raise ValueError("Signature webhook invalide")

    def _build_task_description(self, title: str, body: str) -> str:
        """Construire la description complète pour l'agent supervisor."""
        desc = f"# {title}\n\n"
        if body:
            desc += body
        else:
            desc += "(Aucune description fournie dans l'issue)"
        return desc

    def _make_branch_name(self, issue_number: int, title: str) -> str:
        """
        Générer un nom de branche Git valide à partir du numéro d'issue et du titre.
        Format : task/{issue_number}-{slug}
        """
        # Nettoyer le titre pour en faire un slug
        slug = title.lower()
        # Remplacer les espaces et caractères spéciaux par des tirets
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        slug = slug.strip("-")
        # Tronquer à 40 caractères max
        slug = slug[:40].rstrip("-")

        return f"task/{issue_number}-{slug}"

    async def _run_pipeline(
        self,
        issue_number: int,
        issue_title: str,
        task_description: str,
        branch_name: str,
        repo_owner: str,
        repo_name: str,
    ) -> None:
        """
        Pipeline complet d'une tâche agent :
        1. Créer la branche Git
        2. Préparer le workspace
        3. Spawn le container agent
        4. Monitor jusqu'à la fin
        5. Créer la PR
        6. Nettoyer
        """
        task_id = f"{repo_owner}-{repo_name}-{issue_number}"
        log = logger.bind(task_id=task_id, issue=issue_number)

        # Enregistrer la tâche
        self.task_monitor.register_task(task_id, {
            "issue_number": issue_number,
            "issue_title": issue_title,
            "branch_name": branch_name,
            "status": "initializing",
        })

        workspace_path: Optional[str] = None
        container_id: Optional[str] = None

        try:
            # --- Étape 1 : Créer la branche Git ---
            log.info("pipeline_create_branch")
            self.task_monitor.update_task(task_id, {"status": "creating_branch"})
            await self.git_manager.create_branch(
                repo_owner=repo_owner,
                repo_name=repo_name,
                branch_name=branch_name,
                base_branch="main",
            )
            # Commenter sur l'issue
            await self.git_manager.post_issue_comment(
                repo_owner=repo_owner,
                repo_name=repo_name,
                issue_number=issue_number,
                comment=f"Branch `{branch_name}` créée. Démarrage du pipeline agents...",
            )

            # --- Étape 2 : Préparer le workspace ---
            log.info("pipeline_prepare_workspace")
            self.task_monitor.update_task(task_id, {"status": "preparing_workspace"})
            workspace_path = await self.git_manager.clone_branch(
                repo_owner=repo_owner,
                repo_name=repo_name,
                branch_name=branch_name,
                task_id=task_id,
            )

            # --- Étape 3 : Spawn container agent ---
            log.info("pipeline_spawn_container", workspace=workspace_path)
            self.task_monitor.update_task(task_id, {"status": "running_agents"})
            container_id = await self.container_manager.spawn(
                task_id=task_id,
                workspace_path=workspace_path,
                task_description=task_description,
                issue_number=issue_number,
                branch_name=branch_name,
                repo_owner=repo_owner,
                repo_name=repo_name,
            )

            # --- Étape 4 : Monitor jusqu'à la fin ---
            log.info("pipeline_monitoring", container=container_id)
            result = await self.task_monitor.wait_for_completion(
                task_id=task_id,
                workspace_path=workspace_path,
                container_id=container_id,
                timeout=self.config.TASK_TIMEOUT_SECONDS,
            )

            # --- Crash recovery : respawn si crash détecté ---
            if result.get("crash"):
                log.warning("crash_detected_respawning", task_id=task_id)
                self.task_monitor.update_task(task_id, {"status": "resuming_after_crash", "crash_recovery": True})
                # Commenter sur l'issue
                try:
                    await self.git_manager.post_issue_comment(
                        repo_owner=repo_owner,
                        repo_name=repo_name,
                        issue_number=issue_number,
                        comment="⚠️ Container arrêté inopinément. Reprise depuis le dernier checkpoint...",
                    )
                except Exception:
                    pass
                # Respawn avec RESUME=true — le runtime rechargera le checkpoint SQLite
                container_id = await self.container_manager.spawn(
                    task_id=task_id,
                    workspace_path=workspace_path,
                    task_description=task_description,
                    issue_number=issue_number,
                    branch_name=branch_name,
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                    resume=True,
                )
                log.info("container_respawned", container_id=container_id)
                result = await self.task_monitor.wait_for_completion(
                    task_id=task_id,
                    workspace_path=workspace_path,
                    container_id=container_id,
                    timeout=self.config.TASK_TIMEOUT_SECONDS,
                )

            if not result.get("success"):
                raise RuntimeError(f"Pipeline agent échoué : {result.get('error', 'unknown')}")

            # --- Étape 5 : Push et créer PR ---
            log.info("pipeline_push_and_pr")
            self.task_monitor.update_task(task_id, {"status": "creating_pr"})

            summary = result.get("final_summary", f"Tâche #{issue_number} complétée par les agents AgentForge.")

            # Push des commits (le coder les a déjà commités dans /workspace)
            await self.git_manager.push_branch(
                workspace_path=workspace_path,
                branch_name=branch_name,
            )

            # Créer la Pull Request
            pr_url = await self.git_manager.create_pull_request(
                repo_owner=repo_owner,
                repo_name=repo_name,
                branch_name=branch_name,
                issue_number=issue_number,
                issue_title=issue_title,
                summary=summary,
            )

            self.task_monitor.update_task(task_id, {"status": "completed", "pr_url": pr_url})
            log.info("pipeline_completed", pr_url=pr_url)

            # Commenter sur l'issue avec le lien vers la PR
            await self.git_manager.post_issue_comment(
                repo_owner=repo_owner,
                repo_name=repo_name,
                issue_number=issue_number,
                comment=f"Pipeline terminé. Pull Request créée : {pr_url}\n\n{summary}",
            )

        except Exception as e:
            log.error("pipeline_failed", error=str(e), exc_info=True)
            self.task_monitor.update_task(task_id, {"status": "failed", "error": str(e)})

            # Commenter l'erreur sur l'issue
            try:
                await self.git_manager.post_issue_comment(
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                    issue_number=issue_number,
                    comment=f"Pipeline agent échoué.\n\nErreur : `{e}`\n\nVérifier les logs avec `make logs-orch`.",
                )
            except Exception:
                pass

        finally:
            # --- Étape 6 : Nettoyage ---
            if container_id:
                try:
                    await self.container_manager.destroy(container_id)
                except Exception as e:
                    log.warning("cleanup_container_failed", error=str(e))
            if workspace_path:
                try:
                    await self.git_manager.cleanup_workspace(workspace_path)
                except Exception as e:
                    log.warning("cleanup_workspace_failed", error=str(e))
