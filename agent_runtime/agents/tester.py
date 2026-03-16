"""
tester.py — Agent Tester
Exécute les tests existants, analyse les erreurs, écrit son rapport dans l'état.
"""

import os
from typing import Any, Dict

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel

from state import TaskState
from tools.shell_tools import make_shell_tools, _detect_test_command

logger = structlog.get_logger()

TESTER_SYSTEM_PROMPT = """Tu es un ingénieur QA expert, partie d'un système multi-agents.

Ton rôle est d'analyser les résultats des tests et de produire un rapport clair.

## Tes responsabilités

1. **Analyser la sortie des tests** : Comprendre ce qui passe et ce qui échoue
2. **Identifier les causes racines** : Pas juste "le test X a échoué" mais pourquoi
3. **Produire un rapport structuré** : Pour aider le Reviewer à prendre une décision

## Format de réponse

```markdown
## Rapport de tests

### Résultat global
[PASS / FAIL / PARTIAL]

### Tests exécutés
- Total : X
- Passés : Y
- Échoués : Z

### Détail des échecs
[Si des tests échouent, lister chacun avec la cause probable]

### Analyse
[Interprétation des résultats : le code est-il correct ? Y a-t-il des erreurs résiduelles ?]

### Recommandation pour le Reviewer
[APPROVE / FIX_NEEDED — avec explication courte]
```
"""


def run_tester(state: TaskState, llm: BaseChatModel) -> Dict[str, Any]:
    """
    Nœud Tester du graphe LangGraph.

    Exécute les tests, analyse les résultats, produit un rapport.
    """
    log = logger.bind(agent="tester", issue=state.get("issue_number"))
    log.info("tester_start")

    repo_path = state["repo_path"]
    shell_tools = make_shell_tools(repo_path)

    # --- Étape 1 : Installer les dépendances si nécessaire ---
    test_output = ""
    install_result = _try_install_deps(repo_path)
    if install_result:
        test_output += f"=== Installation dépendances ===\n{install_result}\n\n"

    # --- Étape 2 : Détecter et exécuter les tests ---
    test_command = _detect_test_command(repo_path)

    if not test_command:
        log.info("tester_no_tests_found")
        raw_output = "(Aucun framework de test détecté dans le repo)"
        tests_passed = True  # Pas de tests = pas d'échec
    else:
        log.info("tester_running", command=test_command)
        import subprocess
        result = subprocess.run(
            test_command,
            shell=True,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        raw_output = result.stdout + "\n" + result.stderr
        tests_passed = result.returncode == 0
        log.info("tester_done", passed=tests_passed, rc=result.returncode)

    test_output += raw_output

    # Limiter la sortie
    if len(test_output) > 4000:
        test_output = test_output[:2000] + "\n\n[... sortie tronquée ...]\n\n" + test_output[-2000:]

    # --- Étape 3 : Faire analyser les résultats par le LLM ---
    analysis = _analyze_test_output(
        llm=llm,
        test_output=test_output,
        test_command=test_command or "aucun",
        tests_passed=tests_passed,
        task_description=state["task_description"],
    )

    full_output = f"Commande : `{test_command or 'N/A'}`\n\n{test_output}\n\n{analysis}"

    log.info("tester_analysis_done", passed=tests_passed)

    return {
        "test_output": full_output,
        "tests_passed": tests_passed,
    }


def _try_install_deps(repo_path: str) -> str:
    """Tenter d'installer les dépendances (silencieux si déjà installées)."""
    import subprocess

    commands = []
    if os.path.exists(os.path.join(repo_path, "requirements.txt")):
        commands.append("pip install -r requirements.txt -q")
    elif os.path.exists(os.path.join(repo_path, "package.json")):
        commands.append("npm install --silent")

    for cmd in commands:
        try:
            result = subprocess.run(
                cmd, shell=True, cwd=repo_path,
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                return f"Avertissement installation : {result.stderr[:200]}"
        except Exception as e:
            return f"Erreur installation : {e}"
    return ""


def _analyze_test_output(
    llm: BaseChatModel,
    test_output: str,
    test_command: str,
    tests_passed: bool,
    task_description: str,
) -> str:
    """Faire analyser la sortie des tests par le LLM."""
    status = "PASS" if tests_passed else "FAIL"

    user_message = f"""## Sortie des tests

Commande : `{test_command}`
Statut global : **{status}**

```
{test_output}
```

## Tâche qui a été implémentée

{task_description[:500]}

Analyse ces résultats et produis le rapport structuré demandé.
"""

    try:
        messages = [
            SystemMessage(content=TESTER_SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ]
        response = llm.invoke(messages)
        return "\n\n" + response.content.strip()
    except Exception as e:
        logger.warning("tester_analysis_failed", error=str(e))
        status_str = "PASS" if tests_passed else "FAIL"
        return f"\n\n## Rapport automatique\n\nStatut : {status_str}\n\nSortie brute analysée manuellement."
