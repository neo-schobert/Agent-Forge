"""
state.py — Définition de l'état partagé du graphe LangGraph

TaskState est le TypedDict qui circule entre tous les nœuds du graphe.
Chaque agent lit et enrichit cet état.
"""

from typing import Annotated, List, Optional
from typing_extensions import TypedDict


class TaskState(TypedDict):
    """
    État partagé de la tâche, transmis entre tous les agents du graphe.

    Champs initialisés par l'orchestrateur avant le lancement du graphe,
    puis enrichis progressivement par chaque agent.
    """

    # --- Entrée (initialisé par l'orchestrateur) ---
    task_description: str
    """Description complète de la tâche (titre + corps de l'issue Forgejo)."""

    repo_path: str
    """Chemin absolu vers le workspace cloné dans la microVM (/workspace)."""

    issue_number: int
    """Numéro de l'issue Forgejo qui a déclenché cette tâche."""

    branch_name: str
    """Nom de la branche Git créée pour cette tâche (task/{n}-{slug})."""

    repo_owner: str
    """Owner du repo Forgejo (généralement l'admin)."""

    repo_name: str
    """Nom du repo Forgejo (agentforge-workspace)."""

    # --- Plan (rempli par Supervisor et Architect) ---
    plan: str
    """
    Plan détaillé de l'implémentation produit par l'Architect.
    Inclut : fichiers à créer/modifier, approche technique, dépendances.
    """

    is_micro_task: bool
    """
    Si True, le Supervisor a déterminé que c'est une micro-tâche :
    on saute l'Architect et on va directement au Coder.
    """

    # --- Implémentation (rempli par Coder) ---
    code_changes: List[str]
    """Liste des fichiers créés ou modifiés par le Coder."""

    commit_messages: List[str]
    """Liste des messages de commit créés par le Coder."""

    # --- Tests (rempli par Tester) ---
    test_output: str
    """Sortie complète des tests exécutés (pytest, npm test, etc.)."""

    tests_passed: bool
    """True si tous les tests ont passé."""

    # --- Review (rempli par Reviewer) ---
    review_feedback: str
    """
    Feedback du Reviewer.
    - Contient "LGTM" ou "approved" si le code est validé.
    - Sinon, feedback actionnable pour le Coder.
    """

    review_approved: bool
    """True si le Reviewer a approuvé le code."""

    # --- Contrôle de flux ---
    iterations: int
    """
    Compteur de boucles Coder → Tester → Reviewer.
    Forcé à END si iterations >= 3 (évite les boucles infinies).
    """

    done: bool
    """True quand le pipeline est terminé (approuvé ou iterations >= 3)."""

    # --- Résultat final ---
    final_summary: str
    """
    Résumé final produit par le Reviewer, utilisé comme corps de la PR.
    """

    error: Optional[str]
    """Si non None, une erreur fatale s'est produite pendant le pipeline."""
