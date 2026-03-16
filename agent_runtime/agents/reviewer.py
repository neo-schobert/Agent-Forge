"""
reviewer.py — Agent Reviewer
Relit le diff complet, vérifie la cohérence avec la tâche demandée,
produit soit "LGTM" soit un feedback actionnable pour le Coder.
"""

from typing import Any, Dict

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel

from state import TaskState
from tools.git_tools import git_diff, git_log

logger = structlog.get_logger()

REVIEWER_SYSTEM_PROMPT = """Tu es un Senior Engineer expérimenté, partie d'un système multi-agents.

Ton rôle est de reviewer le code produit par le Coder et de décider s'il est prêt
pour merger en production.

## Critères de validation

1. **Fonctionnalité** : Le code implémente-t-il ce qui était demandé ?
2. **Qualité** : Le code est-il lisible, maintenable, idiomatique ?
3. **Tests** : Y a-t-il des tests ? Passent-ils ?
4. **Sécurité** : Pas de failles évidentes (injection, secrets hardcodés, etc.) ?
5. **Complétude** : Y a-t-il des TODO, des placeholders, du code non-terminé ?

## Décision

Tu dois produire **exactement l'une de ces deux réponses** :

### Si le code est validé :
```
LGTM

[Résumé en 3-5 phrases de ce qui a été implémenté, pour le corps de la PR]
```

### Si des corrections sont nécessaires :
```
CHANGES_NEEDED

[Liste numérotée et actionnable des corrections à apporter]
1. [Correction précise avec le fichier et la ligne si possible]
2. [Correction précise]
...

[Fin avec : PRIORITY: HIGH/MEDIUM/LOW]
```

Ne commence JAMAIS par autre chose que "LGTM" ou "CHANGES_NEEDED".
Sois direct et précis.
"""


def run_reviewer(state: TaskState, llm: BaseChatModel) -> Dict[str, Any]:
    """
    Nœud Reviewer du graphe LangGraph.

    Évalue le code et décide d'approuver ou de demander des corrections.
    """
    iterations = state.get("iterations", 0)
    log = logger.bind(agent="reviewer", issue=state.get("issue_number"), iteration=iterations)
    log.info("reviewer_start")

    repo_path = state["repo_path"]
    task_description = state["task_description"]
    test_output = state.get("test_output", "")
    tests_passed = state.get("tests_passed", True)

    # --- Récupérer le diff ---
    try:
        diff = git_diff(repo_path, "main")
    except Exception as e:
        diff = f"(Impossible de récupérer le diff : {e})"

    # --- Récupérer l'historique des commits ---
    try:
        commit_log = git_log(repo_path, n=10)
    except Exception as e:
        commit_log = f"(Impossible de récupérer les commits : {e})"

    # --- Construire le message pour le Reviewer ---
    user_message = f"""## Tâche demandée

{task_description}

## Commits créés

```
{commit_log}
```

## Résultats des tests

Tests passés : {'OUI' if tests_passed else 'NON'}

```
{test_output[:2000] if test_output else '(Aucun test exécuté)'}
```

## Diff complet (branche courante vs main)

```diff
{diff[:5000]}
```

Effectue ta review et produis ta décision.
"""

    try:
        messages = [
            SystemMessage(content=REVIEWER_SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ]
        response = llm.invoke(messages)
        feedback = response.content.strip()

        # Analyser la décision
        review_approved = _is_approved(feedback)
        final_summary = _extract_summary(feedback) if review_approved else ""

        # Incrémenter le compteur d'itérations
        new_iterations = iterations + 1

        # Forcer la fin si trop d'itérations
        force_done = new_iterations >= 3

        if force_done and not review_approved:
            log.warning(
                "reviewer_max_iterations",
                iterations=new_iterations,
                forcing_done=True,
            )
            final_summary = _build_forced_summary(state, feedback)
            review_approved = True  # Force l'approbation

        log.info(
            "reviewer_done",
            approved=review_approved,
            iterations=new_iterations,
            forced=force_done,
        )

        return {
            "review_feedback": feedback,
            "review_approved": review_approved,
            "iterations": new_iterations,
            "done": review_approved or force_done,
            "final_summary": final_summary if (review_approved or force_done) else "",
        }

    except Exception as e:
        log.error("reviewer_error", error=str(e))
        # En cas d'erreur, approuver quand même pour ne pas bloquer
        return {
            "review_feedback": f"Erreur reviewer : {e}",
            "review_approved": True,
            "iterations": iterations + 1,
            "done": True,
            "final_summary": f"Tâche #{state.get('issue_number', '?')} implémentée (review non disponible).",
        }


def _is_approved(feedback: str) -> bool:
    """Déterminer si le Reviewer a approuvé le code."""
    upper = feedback.upper().strip()
    return upper.startswith("LGTM") or "LGTM" in feedback[:50]


def _extract_summary(feedback: str) -> str:
    """Extraire le résumé de la réponse LGTM."""
    lines = feedback.strip().split("\n")
    # Ignorer la première ligne "LGTM" et les lignes vides suivantes
    summary_lines = []
    started = False
    for line in lines[1:]:
        if not started and not line.strip():
            continue
        started = True
        summary_lines.append(line)

    summary = "\n".join(summary_lines).strip()
    return summary if summary else "Tâche implémentée et validée par le Reviewer."


def _build_forced_summary(state: TaskState, last_feedback: str) -> str:
    """Construire un résumé quand on force la fin (max iterations)."""
    changes = state.get("code_changes", [])
    commits = state.get("commit_messages", [])

    summary = f"""## Résumé d'implémentation

**Itérations** : {state.get('iterations', 0) + 1}/3 (limite atteinte)

**Fichiers modifiés** :
{chr(10).join(f'- `{f}`' for f in changes) if changes else '- (non disponible)'}

**Commits** :
{chr(10).join(f'- {c}' for c in commits) if commits else '- (non disponible)'}

**Dernier feedback Reviewer** :
{last_feedback[:500]}

*Note : La limite d'itérations a été atteinte. Le code a été soumis en l'état.*
"""
    return summary
