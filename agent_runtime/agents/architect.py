"""
architect.py — Agent Architect
Lit le repo existant, comprend la structure, produit un plan détaillé :
quels fichiers créer/modifier, quelle approche technique, quelles dépendances.
"""

import os
from typing import Any, Dict, List

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel

from state import TaskState

logger = structlog.get_logger()

ARCHITECT_SYSTEM_PROMPT = """Tu es un Architect logiciel expert, partie d'un système multi-agents.

Ton rôle est de produire un plan d'implémentation **détaillé et actionnable** pour le Coder.

## Tes responsabilités

1. **Explorer le repo existant** : Comprendre la structure, les conventions, les dépendances existantes
2. **Analyser la tâche** : Décomposer en étapes concrètes
3. **Produire un plan précis** : Liste de fichiers à créer/modifier avec leur contenu attendu

## Format de réponse

Réponds avec un plan structuré en Markdown :

```markdown
## Plan d'implémentation

### Analyse du repo existant
[Description de ce que tu as observé : structure, frameworks utilisés, conventions]

### Approche technique
[Explication de l'approche choisie et pourquoi]

### Fichiers à créer / modifier

#### 1. `chemin/vers/fichier.py` (CRÉER)
**Rôle** : [Description]
**Contenu attendu** :
- [Élément 1]
- [Élément 2]

#### 2. `autre/fichier.py` (MODIFIER)
**Rôle** : [Description]
**Changements** :
- [Changement 1]

### Dépendances à ajouter
[Liste des packages à ajouter si nécessaire, ou "Aucune"]

### Ordre d'implémentation
1. [Premier fichier à créer/modifier]
2. [Deuxième]
...

### Points d'attention
[Pièges à éviter, contraintes à respecter]
```

Sois précis et concret. Le Coder doit pouvoir implémenter sans ambiguïté.
"""


def run_architect(state: TaskState, llm: BaseChatModel) -> Dict[str, Any]:
    """
    Nœud Architect du graphe LangGraph.

    Analyse le repo et produit un plan détaillé d'implémentation.
    """
    log = logger.bind(agent="architect", issue=state.get("issue_number"))
    log.info("architect_start")

    task_description = state["task_description"]
    repo_path = state["repo_path"]
    high_level_plan = state.get("plan", "")

    # Construire le contexte du repo
    repo_context = _build_repo_context(repo_path)

    user_message = f"""## Tâche

{task_description}

## Plan de haut niveau (du Supervisor)

{high_level_plan}

## Contexte du repo

{repo_context}

Produis le plan d'implémentation détaillé.
"""

    try:
        messages = [
            SystemMessage(content=ARCHITECT_SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ]
        response = llm.invoke(messages)
        detailed_plan = response.content.strip()

        log.info("architect_done", plan_length=len(detailed_plan))

        return {"plan": detailed_plan}

    except Exception as e:
        log.error("architect_error", error=str(e))
        # Fallback : plan minimal
        fallback_plan = f"""## Plan d'implémentation (fallback)

### Tâche
{task_description}

### Approche
Implémenter directement selon la description de la tâche.
Analyser le repo, créer/modifier les fichiers nécessaires, écrire les tests.
"""
        return {"plan": fallback_plan}


def _build_repo_context(repo_path: str) -> str:
    """
    Construire un contexte riche sur le repo existant :
    - Structure des fichiers
    - Contenu des fichiers clés (README, package.json, requirements.txt, etc.)
    """
    context_parts = []

    # --- Structure des fichiers ---
    file_tree = _get_file_tree(repo_path)
    context_parts.append(f"### Structure des fichiers\n```\n{file_tree}\n```")

    # --- Fichiers clés à lire ---
    key_files = [
        "README.md",
        "requirements.txt",
        "package.json",
        "setup.py",
        "pyproject.toml",
        "go.mod",
        "Cargo.toml",
        "Makefile",
        ".env.example",
    ]

    for filename in key_files:
        filepath = os.path.join(repo_path, filename)
        if os.path.exists(filepath):
            try:
                content = open(filepath, encoding="utf-8", errors="replace").read()
                if len(content) > 2000:
                    content = content[:2000] + "\n[... tronqué ...]"
                context_parts.append(f"### {filename}\n```\n{content}\n```")
            except Exception:
                pass

    # --- Contenu des fichiers source (limité) ---
    source_files = _get_source_files(repo_path)
    for filepath in source_files[:5]:  # Max 5 fichiers source
        try:
            content = open(filepath, encoding="utf-8", errors="replace").read()
            if len(content) > 3000:
                content = content[:3000] + "\n[... tronqué ...]"
            rel_path = os.path.relpath(filepath, repo_path)
            context_parts.append(f"### {rel_path}\n```\n{content}\n```")
        except Exception:
            pass

    return "\n\n".join(context_parts) if context_parts else "(repo vide)"


def _get_file_tree(repo_path: str, max_files: int = 80) -> str:
    """Générer un arbre de fichiers."""
    lines = []
    count = 0
    for root, dirs, files in os.walk(repo_path):
        # Ignorer les dossiers cachés et de build
        dirs[:] = sorted([
            d for d in dirs
            if not d.startswith(".")
            and d not in ("node_modules", "__pycache__", "dist", "build", ".git", "venv")
        ])

        level = root.replace(repo_path, "").count(os.sep)
        indent = "  " * level
        folder = os.path.basename(root)
        if level > 0:
            lines.append(f"{indent}{folder}/")

        sub_indent = "  " * (level + 1)
        for filename in sorted(files):
            if not filename.startswith("."):
                lines.append(f"{sub_indent}{filename}")
                count += 1
                if count >= max_files:
                    lines.append(f"{sub_indent}[... et plus de fichiers ...]")
                    return "\n".join(lines)

    return "\n".join(lines) if lines else "(vide)"


def _get_source_files(repo_path: str) -> List[str]:
    """Récupérer les fichiers source principaux."""
    extensions = {".py", ".js", ".ts", ".go", ".rs", ".java", ".cpp", ".c", ".rb"}
    source_files = []

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".")
            and d not in ("node_modules", "__pycache__", "dist", "build", ".git")
        ]
        for filename in files:
            _, ext = os.path.splitext(filename)
            if ext in extensions:
                source_files.append(os.path.join(root, filename))

    # Trier par taille (les plus petits fichiers en premier — plus souvent utiles)
    source_files.sort(key=lambda f: os.path.getsize(f))
    return source_files
