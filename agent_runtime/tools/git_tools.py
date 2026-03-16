"""
git_tools.py — Outils Git pour les agents (Coder principalement)
Fonctions wrappées pour être utilisées comme LangChain tools.
"""

import os
import subprocess
from typing import List

from langchain_core.tools import tool


def _run_git(repo_path: str, args: List[str]) -> str:
    """Exécuter une commande git dans le workspace et retourner la sortie."""
    result = subprocess.run(
        ["git"] + args,
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} a échoué (rc={result.returncode}) :\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result.stdout.strip()


def git_add(repo_path: str, files: List[str]) -> str:
    """Stager des fichiers pour le prochain commit."""
    return _run_git(repo_path, ["add"] + files)


def git_commit(repo_path: str, message: str) -> str:
    """
    Créer un commit avec le message donné.
    Retourne le hash du commit.
    """
    _run_git(repo_path, ["commit", "-m", message])
    return _run_git(repo_path, ["rev-parse", "HEAD"])[:8]


def git_diff(repo_path: str, base: str = "main") -> str:
    """
    Retourner le diff complet entre la branche courante et la base.
    Limité à 8000 caractères pour ne pas dépasser le contexte LLM.
    """
    diff = _run_git(repo_path, ["diff", base, "--stat", "--diff-filter=ACMR"])
    full_diff = _run_git(repo_path, ["diff", base, "--diff-filter=ACMR"])
    summary = f"=== STAT ===\n{diff}\n\n=== DIFF ===\n{full_diff}"
    # Tronquer si trop long
    if len(summary) > 8000:
        summary = summary[:8000] + "\n\n[... diff tronqué ...]"
    return summary


def git_log(repo_path: str, n: int = 5) -> str:
    """Afficher les derniers commits."""
    return _run_git(repo_path, ["log", f"-{n}", "--oneline"])


def git_status(repo_path: str) -> str:
    """Afficher le statut du working tree."""
    return _run_git(repo_path, ["status", "--short"])


def git_add_and_commit(repo_path: str, files: List[str], message: str) -> str:
    """
    Stager les fichiers et créer un commit en une seule opération.
    C'est la fonction principale utilisée par le Coder.
    """
    if not files:
        # Stager tous les changements si liste vide
        _run_git(repo_path, ["add", "-A"])
    else:
        git_add(repo_path, files)

    # Vérifier qu'il y a quelque chose à commiter
    status = git_status(repo_path)
    if not status and not _has_staged_changes(repo_path):
        return "Rien à commiter (aucun changement détecté)"

    return git_commit(repo_path, message)


def _has_staged_changes(repo_path: str) -> bool:
    """Vérifier si des changements sont stagés."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_path,
        capture_output=True,
    )
    return result.returncode != 0  # returncode 1 = changements stagés


# =============================================================================
# LangChain Tools (décorateur @tool)
# Ces fonctions sont exposées aux agents via le mécanisme tool calling.
# =============================================================================

def make_git_tools(repo_path: str):
    """
    Factory : crée les tools LangChain bindés sur un repo_path spécifique.
    """

    @tool
    def commit_files(files: List[str], message: str) -> str:
        """
        Stager les fichiers listés et créer un commit Git.

        Args:
            files: Liste des chemins de fichiers à commiter (relatifs au workspace).
                   Passer [] pour stager tous les changements.
            message: Message de commit descriptif (convention: 'feat: ...', 'fix: ...').

        Returns:
            Hash court du commit créé, ou message d'erreur.
        """
        try:
            result = git_add_and_commit(repo_path, files, message)
            return f"Commit créé : {result}"
        except Exception as e:
            return f"Erreur commit : {e}"

    @tool
    def show_git_status() -> str:
        """
        Afficher le statut Git du workspace (fichiers modifiés, non-trackés, etc.).
        """
        try:
            return git_status(repo_path) or "Workspace propre (aucun changement)"
        except Exception as e:
            return f"Erreur git status : {e}"

    @tool
    def show_diff(base_branch: str = "main") -> str:
        """
        Afficher le diff entre la branche courante et la branche de base.

        Args:
            base_branch: Branche de base pour le diff (défaut: 'main').
        """
        try:
            return git_diff(repo_path, base_branch)
        except Exception as e:
            return f"Erreur git diff : {e}"

    @tool
    def show_recent_commits(n: int = 5) -> str:
        """
        Afficher les N derniers commits.

        Args:
            n: Nombre de commits à afficher (défaut: 5).
        """
        try:
            return git_log(repo_path, n)
        except Exception as e:
            return f"Erreur git log : {e}"

    return [commit_files, show_git_status, show_diff, show_recent_commits]
