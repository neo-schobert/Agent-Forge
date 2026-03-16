"""
git_manager.py — Opérations Git via API Forgejo + git CLI
- Créer / supprimer des branches
- Cloner un repo dans un workspace temporaire
- Pusher des commits
- Créer des Pull Requests
- Poster des commentaires sur les issues
"""

import asyncio
import os
import shutil
import tempfile
from typing import Optional

import httpx
import structlog

logger = structlog.get_logger()


class GitManager:
    def __init__(self, config):
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def api_base(self) -> str:
        return f"{self.config.FORGEJO_BASE_URL}/api/v1"

    def _get_auth_headers(self) -> dict:
        """Retourner les headers d'authentification pour l'API Forgejo."""
        if self.config.FORGEJO_API_TOKEN:
            return {"Authorization": f"token {self.config.FORGEJO_API_TOKEN}"}
        # Fallback basic auth géré par le client httpx
        return {}

    def _get_client(self) -> httpx.AsyncClient:
        """Retourner (ou créer) le client HTTP."""
        if self._client is None or self._client.is_closed:
            auth = None
            if not self.config.FORGEJO_API_TOKEN:
                auth = (self.config.FORGEJO_ADMIN_USER, self.config.FORGEJO_ADMIN_PASS)
            self._client = httpx.AsyncClient(
                base_url=self.api_base,
                headers=self._get_auth_headers(),
                auth=auth,
                timeout=30.0,
            )
        return self._client

    async def check_connection(self) -> bool:
        """Vérifier la connexion à Forgejo."""
        try:
            client = self._get_client()
            resp = await client.get("/user")
            if resp.status_code == 200:
                user = resp.json()
                logger.info("forgejo_connected", user=user.get("login"))
                return True
            logger.warning("forgejo_auth_failed", status=resp.status_code)
            return False
        except Exception as e:
            logger.warning("forgejo_connection_error", error=str(e))
            return False

    async def create_branch(
        self,
        repo_owner: str,
        repo_name: str,
        branch_name: str,
        base_branch: str = "main",
    ) -> None:
        """Créer une branche via l'API Forgejo."""
        client = self._get_client()
        payload = {
            "new_branch_name": branch_name,
            "old_branch_name": base_branch,
        }
        resp = await client.post(
            f"/repos/{repo_owner}/{repo_name}/branches",
            json=payload,
        )
        if resp.status_code in (200, 201):
            logger.info("branch_created", branch=branch_name, repo=f"{repo_owner}/{repo_name}")
        elif resp.status_code == 409:
            logger.info("branch_already_exists", branch=branch_name)
        else:
            raise RuntimeError(
                f"Impossible de créer la branche '{branch_name}' : "
                f"HTTP {resp.status_code} — {resp.text[:200]}"
            )

    async def clone_branch(
        self,
        repo_owner: str,
        repo_name: str,
        branch_name: str,
        task_id: str,
    ) -> str:
        """
        Cloner la branche dans un dossier workspace temporaire.
        Retourne le chemin du workspace.
        """
        workspaces_dir = self.config.WORKSPACES_DIR
        os.makedirs(workspaces_dir, exist_ok=True)

        workspace_path = os.path.join(workspaces_dir, task_id)
        if os.path.exists(workspace_path):
            shutil.rmtree(workspace_path)
        os.makedirs(workspace_path)

        # URL de clone (HTTP, depuis l'intérieur du réseau Docker)
        clone_url = (
            f"http://{self.config.FORGEJO_ADMIN_USER}:{self.config.FORGEJO_ADMIN_PASS}"
            f"@{self.config.FORGEJO_BASE_URL.replace('http://', '')}"
            f"/{repo_owner}/{repo_name}.git"
        )

        cmd = [
            "git", "clone",
            "--branch", branch_name,
            "--depth", "50",
            clone_url,
            workspace_path,
        ]

        logger.info("cloning_branch", branch=branch_name, workspace=workspace_path)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(
                f"git clone échoué (rc={proc.returncode}) : {stderr.decode()[:300]}"
            )

        # Configurer git dans le workspace pour les commits des agents
        await self._git_config(workspace_path, "user.email", "agents@agentforge.local")
        await self._git_config(workspace_path, "user.name", "AgentForge Bot")

        # Configurer le remote avec credentials pour le push
        await self._run_git(
            workspace_path,
            ["remote", "set-url", "origin", clone_url],
        )

        logger.info("clone_complete", workspace=workspace_path)
        return workspace_path

    async def push_branch(self, workspace_path: str, branch_name: str) -> None:
        """Pusher les commits locaux vers Forgejo."""
        logger.info("pushing_branch", branch=branch_name)
        await self._run_git(
            workspace_path,
            ["push", "origin", branch_name, "--force-with-lease"],
        )
        logger.info("push_complete", branch=branch_name)

    async def create_pull_request(
        self,
        repo_owner: str,
        repo_name: str,
        branch_name: str,
        issue_number: int,
        issue_title: str,
        summary: str,
    ) -> str:
        """
        Créer une Pull Request via l'API Forgejo.
        Retourne l'URL de la PR.
        """
        client = self._get_client()

        pr_title = f"[Agent] {issue_title}"
        pr_body = f"""## Résumé

Closes #{issue_number}

{summary}

---
*Pull Request générée automatiquement par [AgentForge](https://github.com/user/agentforge).*
*Pipeline : Supervisor → Architect → Coder → Tester → Reviewer*
"""

        payload = {
            "title": pr_title,
            "body": pr_body,
            "head": branch_name,
            "base": "main",
            "labels": [],
        }

        resp = await client.post(
            f"/repos/{repo_owner}/{repo_name}/pulls",
            json=payload,
        )

        if resp.status_code in (200, 201):
            pr_data = resp.json()
            pr_url = pr_data.get("html_url", "")
            pr_number = pr_data.get("number", "?")
            logger.info("pr_created", pr=pr_number, url=pr_url)
            return pr_url
        else:
            raise RuntimeError(
                f"Impossible de créer la PR : HTTP {resp.status_code} — {resp.text[:300]}"
            )

    async def post_issue_comment(
        self,
        repo_owner: str,
        repo_name: str,
        issue_number: int,
        comment: str,
    ) -> None:
        """Poster un commentaire sur une issue Forgejo."""
        client = self._get_client()
        resp = await client.post(
            f"/repos/{repo_owner}/{repo_name}/issues/{issue_number}/comments",
            json={"body": comment},
        )
        if resp.status_code in (200, 201):
            logger.debug("issue_comment_posted", issue=issue_number)
        else:
            logger.warning(
                "issue_comment_failed",
                issue=issue_number,
                status=resp.status_code,
                body=resp.text[:100],
            )

    async def cleanup_workspace(self, workspace_path: str) -> None:
        """Supprimer le workspace temporaire."""
        if os.path.exists(workspace_path):
            shutil.rmtree(workspace_path, ignore_errors=True)
            logger.info("workspace_cleaned", path=workspace_path)

    # -------------------------------------------------------------------------
    # Helpers git CLI
    # -------------------------------------------------------------------------

    async def _run_git(self, cwd: str, args: list) -> str:
        """Exécuter une commande git dans un répertoire donné."""
        cmd = ["git"] + args
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} échoué (rc={proc.returncode}) : "
                f"{stderr.decode()[:300]}"
            )
        return stdout.decode().strip()

    async def _git_config(self, cwd: str, key: str, value: str) -> None:
        """Configurer une variable git dans le workspace."""
        await self._run_git(cwd, ["config", key, value])
