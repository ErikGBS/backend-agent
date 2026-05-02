import logging
from functools import partial

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from src.agent.nodes import node_call_model, node_execute_tools, node_human_review, node_reflect
from src.agent.state import AgentState
from src.models.index import GlobalIndex
from src.models.query import AgentQuery, AgentResponse

logger = logging.getLogger(__name__)

# In-memory checkpointer — persists state between graph runs (HITL resume)
# Replace with SqliteSaver or PostgresSaver for multi-process production deployments
_checkpointer = MemorySaver()


# ── Edge conditions ──────────────────────────────────────────────

def _after_model(state: AgentState) -> str:
    last_response = state.get("_last_response")
    if last_response is None:
        return "reflect"
    if last_response.stop_reason == "tool_use" and state["tool_round"] < 10:
        return "execute_tools"
    return "reflect"


def _after_reflect(state: AgentState) -> str:
    """Route to HITL when reflection finds gaps, otherwise END."""
    if state.get("analysis") is None and state.get("reflection") is not None:
        return "call_model"          # auto-retry (reflection injected gap messages)
    reflection = state.get("reflection")
    if reflection and not reflection.approved:
        return "human_review"        # pause and wait for developer decision
    return END


def _after_human_review(state: AgentState) -> str:
    """Route based on what the developer decided."""
    decision = state.get("human_decision", "approve")
    if isinstance(decision, str) and decision.startswith("investigate:"):
        # Developer wants more investigation — inject instruction and retry
        instruction = decision.removeprefix("investigate:").strip()
        logger.info("hitl_investigate instruction=%r", instruction[:80])
        return "call_model"
    # "approve" or anything else → accept analysis
    return END


# ── Graph builder ────────────────────────────────────────────────

def build_graph(client):
    """Build and compile the StateGraph with HITL support."""
    graph = StateGraph(AgentState)

    graph.add_node("call_model",    partial(node_call_model, client=client))
    graph.add_node("execute_tools", node_execute_tools)
    graph.add_node("reflect",       partial(node_reflect, client=client))
    graph.add_node("human_review",  node_human_review)

    graph.set_entry_point("call_model")

    graph.add_conditional_edges(
        "call_model", _after_model,
        {"execute_tools": "execute_tools", "reflect": "reflect"},
    )
    graph.add_edge("execute_tools", "call_model")
    graph.add_conditional_edges(
        "reflect", _after_reflect,
        {"call_model": "call_model", "human_review": "human_review", END: END},
    )
    graph.add_conditional_edges(
        "human_review", _after_human_review,
        {"call_model": "call_model", END: END},
    )

    return graph.compile(checkpointer=_checkpointer)


# ── Public runners ───────────────────────────────────────────────

def _make_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


async def run_graph(
    query: AgentQuery,
    index: GlobalIndex,
    client,
    thread_id: str = "default",
) -> "GraphRunResult":
    """Start or continue a graph run. Returns a GraphRunResult."""
    from src.agent.core import _build_initial_content

    graph = build_graph(client)
    config = _make_config(thread_id)

    # Check if this thread already has state (resume after HITL)
    snapshot = await graph.aget_state(config)
    is_resume = bool(snapshot and snapshot.next)

    if is_resume:
        # Resume: the last node was interrupted — no new initial state needed
        final = await graph.ainvoke(None, config)
    else:
        initial_state: AgentState = {
            "messages": [{"role": "user", "content": _build_initial_content(query)}],
            "query": query,
            "index": index,
            "repos_used": set(),
            "files_used": [],
            "tool_round": 0,
            "reflection_round": 0,
            "analysis": None,
            "reflection": None,
            "human_decision": None,
            "_last_response": None,
        }
        final = await graph.ainvoke(initial_state, config)

    # Check if interrupted (HITL pending)
    snapshot = await graph.aget_state(config)
    interrupted = bool(snapshot and snapshot.next)
    interrupt_payload = None
    if interrupted and snapshot.tasks:
        for task in snapshot.tasks:
            if hasattr(task, "interrupts") and task.interrupts:
                interrupt_payload = task.interrupts[0].value
                break

    analysis = final.get("analysis") if isinstance(final, dict) else None
    reflection = final.get("reflection") if isinstance(final, dict) else None
    repos = list(final.get("repos_used", [])) if isinstance(final, dict) else []
    files = final.get("files_used", []) if isinstance(final, dict) else []

    return GraphRunResult(
        thread_id=thread_id,
        interrupted=interrupted,
        interrupt_payload=interrupt_payload,
        response=AgentResponse(
            answer=analysis.markdown if analysis else (
                "Análisis en pausa — esperando revisión humana." if interrupted
                else "El agente no generó un análisis válido."
            ),
            analysis=analysis,
            repos_consulted=repos,
            files_fetched=files,
            reflection_approved=reflection.approved if reflection else None,
            reflection_verdict=reflection.verdict if reflection else None,
        ) if not interrupted else None,
    )


async def resume_graph(
    thread_id: str,
    decision: str,
    client,
) -> "GraphRunResult":
    """Resume an interrupted graph with the human's decision."""
    from langgraph.types import Command

    graph = build_graph(client)
    config = _make_config(thread_id)

    final = await graph.ainvoke(Command(resume=decision), config)

    snapshot = await graph.aget_state(config)
    interrupted = bool(snapshot and snapshot.next)

    analysis = final.get("analysis") if isinstance(final, dict) else None
    reflection = final.get("reflection") if isinstance(final, dict) else None
    repos = list(final.get("repos_used", [])) if isinstance(final, dict) else []
    files = final.get("files_used", []) if isinstance(final, dict) else []

    return GraphRunResult(
        thread_id=thread_id,
        interrupted=interrupted,
        interrupt_payload=None,
        response=AgentResponse(
            answer=analysis.markdown if analysis else "El agente no generó un análisis válido.",
            analysis=analysis,
            repos_consulted=repos,
            files_fetched=files,
            reflection_approved=reflection.approved if reflection else None,
            reflection_verdict=reflection.verdict if reflection else None,
        ),
    )


class GraphRunResult:
    def __init__(self, thread_id, interrupted, interrupt_payload, response):
        self.thread_id = thread_id
        self.interrupted = interrupted
        self.interrupt_payload = interrupt_payload
        self.response = response
