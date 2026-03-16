"""
file_tools.py — Outils de manipulation de fichiers pour les agents
Lecture, écriture, listage de fichiers dans le workspace.
"""

import os
import glob as glob_module
from pathlib import Path
from typing import List, Optional

from langchain_core.tools import tool

# Taille max d'un fichier retourné (pour ne pas saturer le contexte LLM)
MAX_FILE_SIZE_CHARS = 10_000


def make_file_tools(workspace_path: str):
    """
    Factory : crée les tools LangChain bindés sur un workspace_path.
    """

    def _resolve(relative_path: str) -> Path:
        """Résoudre un chemin relatif et vérifier qu'il est dans le workspace."""
        path = Path(workspace_path) / relative_path
        # Vérifier qu'on ne sort pas du workspace (path traversal protection)
        try:
            path.resolve().relative_to(Path(workspace_path).resolve())
        except ValueError:
            raise PermissionError(f"Accès refusé : {relative_path} est hors du workspace")
        return path

    @tool
    def read_file(path: str) -> str:
        """
        Lire le contenu d'un fichier dans le workspace.

        Args:
            path: Chemin du fichier relatif au workspace (ex: 'src/main.py').

        Returns:
            Contenu du fichier, ou message d'erreur.
        """
        try:
            full_path = _resolve(path)
            if not full_path.exists():
                return f"Fichier introuvable : {path}"
            if not full_path.is_file():
                return f"'{path}' n'est pas un fichier"

            content = full_path.read_text(encoding="utf-8", errors="replace")
            if len(content) > MAX_FILE_SIZE_CHARS:
                content = content[:MAX_FILE_SIZE_CHARS] + f"\n\n[... fichier tronqué à {MAX_FILE_SIZE_CHARS} caractères ...]"
            return content
        except PermissionError as e:
            return f"Erreur : {e}"
        except Exception as e:
            return f"Erreur lecture {path} : {e}"

    @tool
    def write_file(path: str, content: str) -> str:
        """
        Écrire du contenu dans un fichier du workspace (crée les dossiers si nécessaire).

        Args:
            path: Chemin du fichier relatif au workspace (ex: 'src/utils.py').
            content: Contenu complet à écrire dans le fichier.

        Returns:
            Confirmation ou message d'erreur.
        """
        try:
            full_path = _resolve(path)
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            size = len(content)
            return f"Fichier écrit : {path} ({size} caractères)"
        except PermissionError as e:
            return f"Erreur : {e}"
        except Exception as e:
            return f"Erreur écriture {path} : {e}"

    @tool
    def list_files(directory: str = ".", pattern: str = "**/*") -> str:
        """
        Lister les fichiers dans un répertoire du workspace.

        Args:
            directory: Répertoire relatif au workspace (défaut: racine).
            pattern: Pattern glob pour filtrer (défaut: tous les fichiers).

        Returns:
            Liste des fichiers trouvés.
        """
        try:
            full_dir = _resolve(directory)
            if not full_dir.is_dir():
                return f"'{directory}' n'est pas un répertoire"

            # Utiliser glob
            files = []
            for f in full_dir.rglob("*"):
                # Ignorer les fichiers cachés et __pycache__
                parts = f.parts
                if any(p.startswith(".") or p == "__pycache__" or p == "node_modules" for p in parts):
                    continue
                if f.is_file():
                    rel = f.relative_to(Path(workspace_path))
                    files.append(str(rel))

            if not files:
                return f"Aucun fichier trouvé dans {directory}"

            # Trier et limiter
            files.sort()
            if len(files) > 100:
                result = "\n".join(files[:100]) + f"\n\n[... et {len(files)-100} autres fichiers]"
            else:
                result = "\n".join(files)
            return result
        except PermissionError as e:
            return f"Erreur : {e}"
        except Exception as e:
            return f"Erreur listage {directory} : {e}"

    @tool
    def file_exists(path: str) -> str:
        """
        Vérifier si un fichier existe dans le workspace.

        Args:
            path: Chemin du fichier relatif au workspace.

        Returns:
            'true' ou 'false'.
        """
        try:
            full_path = _resolve(path)
            return "true" if full_path.exists() else "false"
        except Exception:
            return "false"

    @tool
    def delete_file(path: str) -> str:
        """
        Supprimer un fichier du workspace.

        Args:
            path: Chemin du fichier relatif au workspace.

        Returns:
            Confirmation ou message d'erreur.
        """
        try:
            full_path = _resolve(path)
            if not full_path.exists():
                return f"Fichier introuvable : {path}"
            full_path.unlink()
            return f"Fichier supprimé : {path}"
        except PermissionError as e:
            return f"Erreur : {e}"
        except Exception as e:
            return f"Erreur suppression {path} : {e}"

    return [read_file, write_file, list_files, file_exists, delete_file]
