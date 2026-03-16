"""
graph.py — Définition du graphe LangGraph multi-agents

Topologie : START → supervisor → architect? → coder → tester → reviewer → (coder | END)
Checkpointing via SqliteSaver sur /workspace/.checkpoint.db

Each node receives its own dedicated LLM from the `llms` dict:
  llms["supervisor"], llms["architect"], llms["coder"],
  llms["tester"],     llms["reviewer"]

A "default" key is used as a safe fallback when a specific key is absent.
"""

import os
import sqlite3
from typing import Dict, Literal

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

# Maximum number of Coder → Tester → Reviewer iterations before forced exit
MAX_ITERATIONS = 3


def build_graph(llms: Dict[str, BaseChatModel], workspace_path: str) -> StateGraph:
    """
    Construct and compile the LangGraph state machine.

    Args:
        llms: Dict mapping agent names to their LLM instances.
              Expected keys: supervisor, architect, coder, tester, reviewer.
              Falls back to llms["default"] (or the first value) when a key
              is missing.
        workspace_path: Path to the workspace directory (used for SQLite checkpoint).

    Returns:
        Compiled LangGraph with SQLite checkpointing.
    """

    def _llm(name: str) -> BaseChatModel:
        """Look up a per-agent LLM, falling back to 'default' then first value."""
        if name in llms:
            return llms[name]
        if "default" in llms:
            logger.warning("llm_fallback agent=%s using=default", name)
            return llms["default"]
        # Last resort: first value in the dict
        first = next(iter(llms.values()))
        logger.warning("llm_fallback agent=%s using=first_available", name)
        return first

    # -------------------------------------------------------------------------
    # Node definitions — each closes over its specific LLM instance
    # -------------------------------------------------------------------------

    def supervisor_node(state: TaskState) -> dict:
        return run_supervisor(state, _llm("supervisor"))

    def architect_node(state: TaskState) -> dict:
        return run_architect(state, _llm("architect"))

    def coder_node(state: TaskState) -> dict:
        return run_coder(state, _llm("coder"))

    def tester_node(state: TaskState) -> dict:
        return run_tester(state, _llm("tester"))

    def reviewer_node(state: TaskState) -> dict:
        return run_reviewer(state, _llm("reviewer"))

    # -------------------------------------------------------------------------
    # Routing conditions
    # -------------------------------------------------------------------------

    def route_after_supervisor(
        state: TaskState,
    ) -> Literal["architect", "coder"]:
        """After Supervisor: go to Architect for complex tasks, or skip to Coder."""
        if state.get("is_micro_task", False):
            logger.info("routing", from_="supervisor", to="coder", reason="micro_task")
            return "coder"
        logger.info("routing", from_="supervisor", to="architect")
        return "architect"

    def route_after_reviewer(
        state: TaskState,
    ) -> Literal["coder", "__end__"]:
        """
        After Reviewer:
        - Approved, done flag set, or max iterations reached → END
        - Otherwise → back to Coder with review feedback
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
        logger.info(
            "routing",
            from_="reviewer",
            to="coder",
            feedback_preview=state.get("review_feedback", "")[:50],
        )
        return "coder"

    # -------------------------------------------------------------------------
    # Build the graph
    # -------------------------------------------------------------------------
    graph = StateGraph(TaskState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("architect", architect_node)
    graph.add_node("coder", coder_node)
    graph.add_node("tester", tester_node)
    graph.add_node("reviewer", reviewer_node)

    # Fixed edges
    graph.add_edge(START, "supervisor")
    graph.add_edge("architect", "coder")
    graph.add_edge("coder", "tester")
    graph.add_edge("tester", "reviewer")

    # Conditional edges
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
    # SQLite checkpointing
    # -------------------------------------------------------------------------
    checkpoint_path = os.path.join(workspace_path, ".checkpoint.db")
    conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("graph_compiled", checkpoint=checkpoint_path, agents=list(llms.keys()))
    return compiled


def run_graph(
    llms: Dict[str, BaseChatModel],
    initial_state: TaskState,
    workspace_path: str,
    thread_id: str,
    resume: bool = False,
) -> TaskState:
    """
    Execute the full agent pipeline for a given task.

    Args:
        llms: Dict of per-agent LLM instances
              (keys: supervisor, architect, coder, tester, reviewer, default).
        initial_state: Initial TaskState with task_description, repo_path, etc.
        workspace_path: Path to the workspace directory (for SQLite checkpoint).
        thread_id: Unique run identifier used by the checkpointer.
        resume: If True, resume from existing SQLite checkpoint instead of
                starting fresh. Passes None as input to LangGraph so it loads
                the last checkpointed state.

    Returns:
        Final TaskState after the graph has finished executing.
    """
    graph = build_graph(llms, workspace_path)

    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }

    if resume:
        logger.info(
            "graph_resume_from_checkpoint",
            thread_id=thread_id,
            checkpoint_path=os.path.join(workspace_path, ".checkpoint.db"),
        )
        # Passing None tells LangGraph to resume from the last checkpointed state
        invoke_input = None
    else:
        logger.info(
            "graph_run_start",
            thread_id=thread_id,
            task_preview=initial_state.get("task_description", "")[:100],
            agents=list(llms.keys()),
        )
        invoke_input = initial_state

    final_state = graph.invoke(invoke_input, config=config)

    logger.info(
        "graph_run_complete",
        thread_id=thread_id,
        done=final_state.get("done"),
        iterations=final_state.get("iterations"),
        approved=final_state.get("review_approved"),
        changes=len(final_state.get("code_changes", [])),
    )

    return final_state
