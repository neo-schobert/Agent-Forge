"""
shell_tools.py — Outils d'exécution shell pour les agents
Utilisés principalement par l'agent Tester pour exécuter les tests.

SÉCURITÉ : ces outils s'exécutent dans la microVM Kata (kernel isolé).
La liste des commandes autorisées est restrictive.
"""

import os
import subprocess
import shlex
from typing import List, Optional

from langchain_core.tools import tool

# Timeout max pour une commande shell (en secondes)
SHELL_TIMEOUT = 300

# Commandes autorisées (whitelist)
# Les agents ne peuvent exécuter que ces commandes
ALLOWED_COMMANDS = {
    "python", "python3", "pytest", "pip", "pip3",
    "npm", "npx", "node", "yarn",
    "go", "cargo", "make",
    "bash", "sh",
    "ls", "cat", "echo", "find", "grep",
    "curl",  # pour les tests d'API
}


def _is_allowed(command: str) -> bool:
    """Vérifier que la commande est dans la whitelist."""
    # Extraire le nom du binaire (premier token)
    try:
        tokens = shlex.split(command)
        if not tokens:
            return False
        binary = os.path.basename(tokens[0])
        return binary in ALLOWED_COMMANDS
    except ValueError:
        return False


def make_shell_tools(workspace_path: str):
    """
    Factory : crée les tools LangChain bindés sur un workspace_path.
    """

    def _run_cmd(
        command: str,
        timeout: int = SHELL_TIMEOUT,
        env_extra: Optional[dict] = None,
    ) -> str:
        """Exécuter une commande shell dans le workspace."""
        if not _is_allowed(command):
            return (
                f"Commande non autorisée : '{command.split()[0] if command.split() else command}'\n"
                f"Commandes autorisées : {', '.join(sorted(ALLOWED_COMMANDS))}"
            )

        env = os.environ.copy()
        if env_extra:
            env.update(env_extra)

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr}"

            # Limiter la sortie
            if len(output) > 6000:
                output = output[:3000] + "\n\n[... sortie tronquée ...]\n\n" + output[-3000:]

            # Ajouter le code de retour
            output += f"\n\n[Exit code: {result.returncode}]"
            return output

        except subprocess.TimeoutExpired:
            return f"Timeout ({timeout}s) — commande trop longue : {command[:100]}"
        except Exception as e:
            return f"Erreur exécution : {e}"

    @tool
    def run_tests(test_command: str = "") -> str:
        """
        Exécuter la suite de tests du projet.

        Détecte automatiquement le framework de test (pytest, npm test, go test, cargo test)
        si aucune commande n'est spécifiée. Sinon, utilise la commande fournie.

        Args:
            test_command: Commande de test à exécuter (ex: 'pytest tests/ -v').
                         Laisser vide pour auto-détecter.

        Returns:
            Sortie complète des tests + code de retour.
        """
        if not test_command:
            test_command = _detect_test_command(workspace_path)

        if not test_command:
            return "Aucun framework de test détecté. Créer les tests d'abord."

        return _run_cmd(test_command, timeout=120)

    @tool
    def run_command(command: str) -> str:
        """
        Exécuter une commande shell dans le workspace.

        Commandes autorisées : python, pytest, npm, node, go, cargo, make, bash, etc.

        Args:
            command: Commande à exécuter (ex: 'python -m pytest -v').

        Returns:
            Sortie de la commande + code de retour.
        """
        return _run_cmd(command)

    @tool
    def install_dependencies(package_manager: str = "") -> str:
        """
        Installer les dépendances du projet.

        Détecte automatiquement le package manager si non spécifié
        (pip via requirements.txt, npm via package.json, etc.).

        Args:
            package_manager: 'pip', 'npm', 'yarn', 'cargo', 'go' ou laisser vide pour auto-détecter.
        """
        if not package_manager:
            package_manager = _detect_package_manager(workspace_path)

        if not package_manager:
            return "Aucun gestionnaire de paquets détecté."

        commands = {
            "pip": "pip install -r requirements.txt",
            "npm": "npm install",
            "yarn": "yarn install",
            "cargo": "cargo build",
            "go": "go mod tidy",
        }

        cmd = commands.get(package_manager)
        if not cmd:
            return f"Package manager '{package_manager}' non supporté. Options : {list(commands.keys())}"

        return _run_cmd(cmd, timeout=180)

    return [run_tests, run_command, install_dependencies]


def _detect_test_command(workspace_path: str) -> str:
    """Détecter automatiquement la commande de test en analysant les fichiers du projet."""
    # pytest
    if (
        os.path.exists(os.path.join(workspace_path, "pytest.ini"))
        or os.path.exists(os.path.join(workspace_path, "setup.cfg"))
        or os.path.exists(os.path.join(workspace_path, "pyproject.toml"))
        or any(
            f.startswith("test_") or f.startswith("tests")
            for f in os.listdir(workspace_path)
        )
    ):
        return "python -m pytest -v --tb=short 2>&1"

    # npm test
    if os.path.exists(os.path.join(workspace_path, "package.json")):
        return "npm test 2>&1"

    # go test
    if os.path.exists(os.path.join(workspace_path, "go.mod")):
        return "go test ./... 2>&1"

    # cargo test
    if os.path.exists(os.path.join(workspace_path, "Cargo.toml")):
        return "cargo test 2>&1"

    # Makefile avec cible test
    if os.path.exists(os.path.join(workspace_path, "Makefile")):
        import subprocess
        result = subprocess.run(
            ["grep", "-q", "^test:", "Makefile"],
            cwd=workspace_path, capture_output=True,
        )
        if result.returncode == 0:
            return "make test 2>&1"

    return ""


def _detect_package_manager(workspace_path: str) -> str:
    """Détecter le gestionnaire de paquets."""
    checks = [
        ("requirements.txt", "pip"),
        ("package.json", "npm"),
        ("yarn.lock", "yarn"),
        ("Cargo.toml", "cargo"),
        ("go.mod", "go"),
    ]
    for filename, pm in checks:
        if os.path.exists(os.path.join(workspace_path, filename)):
            return pm
    return ""
