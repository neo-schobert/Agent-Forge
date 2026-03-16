"""
graph.py — Définition du graphe LangGraph multi-agents
Topologie : START → supervisor → architect? → coder → tester → reviewer → (coder | END)
Checkpointing via SqliteSaver sur /workspace/.checkpoint.db
"""

import os
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

import structlog

from state import TaskState
from agents.supervisor import run_supervisor
from agents.architect import run_architect
from agents.coder import run_coder
from agents.tester import run_tester
from agents.reviewer import run_reviewer

logger = structlog.get_logger()

# Nombre max d'itérations Coder → Tester → Reviewer
MAX_ITERATIONS = 3


def build_graph(llm: BaseChatModel, workspace_path: str) -> StateGraph:
    """
    Construire et compiler le graphe LangGraph.

    Args:
        llm: Instance du modèle LLM à utiliser (Anthropic, OpenAI, Ollama)
        workspace_path: Chemin du workspace (pour le checkpoint SQLite)

    Returns:
        Graphe compilé avec checkpointing.
    """

    # -------------------------------------------------------------------------
    # Définir les nœuds (wrappers qui injectent le LLM)
    # -------------------------------------------------------------------------

    def supervisor_node(state: TaskState) -> dict:
        return run_supervisor(state, llm)

    def architect_node(state: TaskState) -> dict:
        return run_architect(state, llm)

    def coder_node(state: TaskState) -> dict:
        return run_coder(state, llm)

    def tester_node(state: TaskState) -> dict:
        return run_tester(state, llm)

    def reviewer_node(state: TaskState) -> dict:
        return run_reviewer(state, llm)

    # -------------------------------------------------------------------------
    # Conditions de routing
    # -------------------------------------------------------------------------

    def route_after_supervisor(
        state: TaskState,
    ) -> Literal["architect", "coder"]:
        """Après le Supervisor : aller à l'Architect ou directement au Coder."""
        if state.get("is_micro_task", False):
            logger.info("routing", from_="supervisor", to="coder", reason="micro_task")
            return "coder"
        else:
            logger.info("routing", from_="supervisor", to="architect")
            return "architect"

    def route_after_reviewer(
        state: TaskState,
    ) -> Literal["coder", "__end__"]:
        """
        Après le Reviewer :
        - Si approuvé ou max iterations → END
        - Sinon → retour au Coder avec le feedback
        """
        review_approved = state.get("review_approved", False)
        done = state.get("done", False)
        iterations = state.get("iterations", 0)

        if done or review_approved or iterations >= MAX_ITERATIONS:
            logger.info(
                "routing",
                from_="reviewer",
                to="end",
                approved=review_approved,
                iterations=iterations,
            )
            return END
        else:
            logger.info(
                "routing",
                from_="reviewer",
                to="coder",
                feedback_preview=state.get("review_feedback", "")[:50],
            )
            return "coder"

    # -------------------------------------------------------------------------
    # Construire le graphe
    # -------------------------------------------------------------------------
    graph = StateGraph(TaskState)

    # Ajouter les nœuds
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("architect", architect_node)
    graph.add_node("coder", coder_node)
    graph.add_node("tester", tester_node)
    graph.add_node("reviewer", reviewer_node)

    # Ajouter les arêtes fixes
    graph.add_edge(START, "supervisor")
    graph.add_edge("architect", "coder")
    graph.add_edge("coder", "tester")
    graph.add_edge("tester", "reviewer")

    # Arêtes conditionnelles
    graph.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {"architect": "architect", "coder": "coder"},
    )

    graph.add_conditional_edges(
        "reviewer",
        route_after_reviewer,
        {"coder": "coder", END: END},
    )

    # -------------------------------------------------------------------------
    # Configurer le checkpointing (SqliteSaver)
    # LangGraph 1.x : SqliteSaver.from_conn_string() retourne un context manager.
    # On doit l'utiliser via __enter__ pour le threading correct.
    # -------------------------------------------------------------------------
    checkpoint_path = os.path.join(workspace_path, ".checkpoint.db")

    # Ouvrir la connexion avec check_same_thread=False pour le threading LangGraph
    import sqlite3
    conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    # Compiler le graphe avec checkpointing
    compiled = graph.compile(checkpointer=checkpointer)

    logger.info("graph_compiled", checkpoint=checkpoint_path)
    return compiled


def run_graph(
    llm: BaseChatModel,
    initial_state: TaskState,
    workspace_path: str,
    thread_id: str,
) -> TaskState:
    """
    Exécuter le graphe complet avec une tâche donnée.

    Args:
        llm: Modèle LLM
        initial_state: État initial avec task_description, repo_path, etc.
        workspace_path: Chemin du workspace pour le checkpoint
        thread_id: Identifiant unique du run (pour le checkpointing)

    Returns:
        État final après exécution du graphe.
    """
    graph = build_graph(llm, workspace_path)

    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }

    logger.info(
        "graph_run_start",
        thread_id=thread_id,
        task_preview=initial_state.get("task_description", "")[:100],
    )

    # Invoquer le graphe
    final_state = graph.invoke(initial_state, config=config)

    logger.info(
        "graph_run_complete",
        thread_id=thread_id,
        done=final_state.get("done"),
        iterations=final_state.get("iterations"),
        approved=final_state.get("review_approved"),
        changes=len(final_state.get("code_changes", [])),
    )

    return final_state
