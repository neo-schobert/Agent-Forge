"""
supervisor.py — Agent Supervisor
Analyse la tâche, détermine le scope, initialise l'état,
décide si une architecture complète est nécessaire ou si c'est une micro-tâche.
"""

import os
from typing import Any, Dict

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel

from state import TaskState

logger = structlog.get_logger()

SUPERVISOR_SYSTEM_PROMPT = """Tu es le Supervisor d'un système multi-agents de développement logiciel.

Ton rôle est d'analyser une tâche de développement et de décider comment l'aborder.

## Tes responsabilités

1. **Analyser la tâche** : Comprendre précisément ce qui est demandé
2. **Évaluer la complexité** : Est-ce une micro-tâche ou une tâche complexe ?
3. **Initialiser le plan de haut niveau** : Une description de l'approche

## Définition d'une micro-tâche
Une micro-tâche est simple et peut être implémentée directement sans phase d'architecture :
- Modifier un seul fichier avec des changements limités
- Corriger un bug simple avec une cause évidente
- Ajouter une petite fonction ou méthode
- Créer un fichier simple (ex: script de 20-50 lignes)

Une tâche complexe nécessite l'Architect pour planifier :
- Créer une nouvelle fonctionnalité avec plusieurs fichiers
- Refactorer une architecture existante
- Implémenter un système avec des interactions entre composants
- Ajouter des dépendances ou modifier la structure du projet

## Format de réponse

Réponds en JSON avec ce format exact :
```json
{
  "is_micro_task": true/false,
  "reasoning": "Explication de ton évaluation",
  "high_level_plan": "Description courte de l'approche (2-5 phrases)"
}
```

Rien d'autre que le JSON.
"""


def run_supervisor(state: TaskState, llm: BaseChatModel) -> Dict[str, Any]:
    """
    Nœud Supervisor du graphe LangGraph.

    Analyse la tâche et détermine si on a besoin de l'Architect
    ou si on peut aller directement au Coder (micro-tâche).
    """
    log = logger.bind(agent="supervisor", issue=state.get("issue_number"))
    log.info("supervisor_start")

    task_description = state["task_description"]
    repo_path = state["repo_path"]

    # Lister les fichiers existants pour donner du contexte au Supervisor
    existing_files = _list_repo_files(repo_path)

    user_message = f"""## Tâche à analyser

{task_description}

## Fichiers existants dans le repo
```
{existing_files}
```

Analyse cette tâche et retourne le JSON demandé.
"""

    try:
        import json
        messages = [
            SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ]
        response = llm.invoke(messages)
        content = response.content.strip()

        # Extraire le JSON de la réponse
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        result = json.loads(content)

        is_micro_task = bool(result.get("is_micro_task", False))
        high_level_plan = result.get("high_level_plan", "")
        reasoning = result.get("reasoning", "")

        log.info(
            "supervisor_done",
            is_micro_task=is_micro_task,
            reasoning=reasoning[:100],
        )

        return {
            "is_micro_task": is_micro_task,
            "plan": high_level_plan,
            "iterations": 0,
            "done": False,
            "code_changes": [],
            "commit_messages": [],
            "error": None,
        }

    except Exception as e:
        log.error("supervisor_error", error=str(e))
        # En cas d'erreur, on continue avec une approche par défaut
        return {
            "is_micro_task": False,
            "plan": f"Implémenter la tâche : {task_description[:200]}",
            "iterations": 0,
            "done": False,
            "code_changes": [],
            "commit_messages": [],
            "error": None,
        }


def _list_repo_files(repo_path: str) -> str:
    """Lister les fichiers du repo de manière concise."""
    files = []
    try:
        for root, dirs, filenames in os.walk(repo_path):
            # Ignorer les dossiers cachés et de build
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in ("node_modules", "__pycache__", "dist", "build", ".git")
            ]
            for filename in filenames:
                if not filename.startswith("."):
                    rel_path = os.path.relpath(
                        os.path.join(root, filename), repo_path
                    )
                    files.append(rel_path)
    except Exception:
        pass

    if not files:
        return "(repo vide ou inaccessible)"

    files.sort()
    if len(files) > 50:
        return "\n".join(files[:50]) + f"\n... et {len(files)-50} autres fichiers"
    return "\n".join(files)
