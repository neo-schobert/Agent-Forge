"""
coder.py — Agent Coder
Implémente le plan fichier par fichier, écrit dans /workspace,
fait un git commit à chaque étape significative.
"""

import os
from typing import Any, Dict

import structlog
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_core.language_models import BaseChatModel

from state import TaskState
from tools.git_tools import make_git_tools, git_add_and_commit, git_status
from tools.file_tools import make_file_tools
from tools.shell_tools import make_shell_tools

logger = structlog.get_logger()

CODER_SYSTEM_PROMPT = """Tu es un développeur expert, partie d'un système multi-agents.

Tu dois implémenter le code selon le plan fourni. Tu as accès à des outils pour
lire/écrire des fichiers et créer des commits Git.

## Tes responsabilités

1. **Lire le plan** : Comprendre exactement ce qui doit être implémenté
2. **Lire les fichiers existants** : Avant de modifier, toujours lire le contenu actuel
3. **Implémenter fichier par fichier** : Créer/modifier chaque fichier
4. **Commiter régulièrement** : Après chaque fichier créé ou modification significative
5. **S'adapter au feedback** : Si le Reviewer t'a donné du feedback, corriger précisément

## Règles importantes

- **Toujours lire un fichier avant de le modifier** : Utilise `read_file` d'abord
- **Commits atomiques** : Un commit par fichier ou par fonctionnalité cohérente
- **Messages de commit descriptifs** : `feat: add user authentication` ou `fix: resolve null pointer in UserService`
- **Code complet** : Écrire des fichiers complets, jamais partiels ou avec des `...` comme placeholder
- **Tests inclus** : Créer des tests pour le code que tu écris
- **Pas de clés hardcodées** : Utiliser des variables d'environnement

## Format de tes actions

Pour chaque action, utilise les outils disponibles directement.
Ne décris pas ce que tu vas faire — fais-le.
"""


def run_coder(state: TaskState, llm: BaseChatModel) -> Dict[str, Any]:
    """
    Nœud Coder du graphe LangGraph.

    Implémente le plan et crée des commits Git.
    Utilise le mode ReAct (Reason + Act) avec les outils.
    """
    log = logger.bind(agent="coder", issue=state.get("issue_number"), iteration=state.get("iterations", 0))
    log.info("coder_start")

    task_description = state["task_description"]
    repo_path = state["repo_path"]
    plan = state.get("plan", "")
    review_feedback = state.get("review_feedback", "")
    iterations = state.get("iterations", 0)

    # Préparer les outils
    git_tools = make_git_tools(repo_path)
    file_tools = make_file_tools(repo_path)
    shell_tools = make_shell_tools(repo_path)
    all_tools = git_tools + file_tools + shell_tools

    # Lier les outils au LLM
    llm_with_tools = llm.bind_tools(all_tools)

    # Préparer le message utilisateur
    if review_feedback and iterations > 0:
        # Mode correction : le Reviewer a donné du feedback
        user_content = f"""## Feedback du Reviewer (itération {iterations})

{review_feedback}

## Plan original

{plan}

## Tâche initiale

{task_description}

Corrige le code selon le feedback du Reviewer.
Lis les fichiers existants avant de les modifier.
Commite chaque correction avec un message descriptif.
"""
    else:
        # Mode initial : implémenter le plan
        user_content = f"""## Plan d'implémentation

{plan}

## Tâche

{task_description}

Implémente ce plan en utilisant les outils disponibles.
Commence par lister les fichiers existants, puis implémente fichier par fichier.
Commite après chaque fichier créé ou modifié.
"""

    messages = [
        SystemMessage(content=CODER_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]

    # Boucle d'exécution ReAct (max 20 tours pour éviter les boucles infinies)
    code_changes = list(state.get("code_changes", []))
    commit_messages = list(state.get("commit_messages", []))
    max_turns = 20

    for turn in range(max_turns):
        log.debug("coder_turn", turn=turn, num_messages=len(messages))

        response = llm_with_tools.invoke(messages)
        messages.append(response)

        # Vérifier si des outils ont été appelés
        if not hasattr(response, "tool_calls") or not response.tool_calls:
            # Pas d'appel d'outil — le Coder a terminé
            log.info("coder_no_more_tools", turn=turn)
            break

        # Exécuter les outils appelés
        from langchain_core.messages import ToolMessage
        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            tool_id = tool_call["id"]

            log.debug("coder_tool_call", tool=tool_name, args=str(tool_args)[:100])

            # Trouver et exécuter l'outil
            tool_result = _execute_tool(all_tools, tool_name, tool_args)

            # Tracker les changements
            if tool_name == "write_file":
                filepath = tool_args.get("path", "")
                if filepath and filepath not in code_changes:
                    code_changes.append(filepath)
            elif tool_name == "commit_files":
                msg = tool_args.get("message", "")
                if msg:
                    commit_messages.append(msg)

            messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_id))

    log.info("coder_done", changes=len(code_changes), commits=len(commit_messages))

    # S'assurer que tout est commité à la fin
    _commit_remaining_changes(repo_path, task_description, iterations)

    return {
        "code_changes": code_changes,
        "commit_messages": commit_messages,
    }


def _execute_tool(tools, tool_name: str, tool_args: dict) -> str:
    """Trouver et exécuter un outil par son nom."""
    for tool in tools:
        if tool.name == tool_name:
            try:
                return tool.invoke(tool_args)
            except Exception as e:
                return f"Erreur outil '{tool_name}' : {e}"
    return f"Outil '{tool_name}' introuvable"


def _commit_remaining_changes(repo_path: str, task_description: str, iteration: int) -> None:
    """Commiter les changements non-stagés restants (filet de sécurité)."""
    try:
        status = git_status(repo_path)
        if status:
            msg = f"chore: work in progress (iteration {iteration})" if iteration > 0 else "feat: implement task"
            git_add_and_commit(repo_path, [], msg)
            logger.debug("coder_safety_commit", message=msg)
    except Exception as e:
        logger.debug("coder_safety_commit_skip", reason=str(e))
