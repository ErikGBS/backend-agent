import logging
from dataclasses import dataclass
from functools import partial

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from src.agent.nodes import node_call_model, node_execute_tools, node_human_review, node_reflect
from src.agent.parsers import build_initial_content
from src.agent.state import AgentState
from src.models.index import GlobalIndex
from src.models.query import AgentQuery, AgentResponse

logger = logging.getLogger(__name__)

_checkpointer = MemorySaver()
_graph_cache: dict = {}  # (id(client), id(index)) → compiled graph


# ── Edge conditions ──────────────────────────────────────────────

def _after_model(state: AgentState) -> str:
    stop_reason = state.get("_stop_reason", "")
    if stop_reason == "tool_use" and state["tool_round"] < 10:
        return "execute_tools"
    return "reflect"


def _after_reflect(state: AgentState) -> str:
    if state.get("analysis") is None and state.get("reflection") is not None:
        return "call_model"          # auto-retry after reflection gaps
    reflection = state.get("reflection")
    if reflection and not reflection.approved:
        return "human_review"
    return END


def _after_human_review(state: AgentState) -> str:
    decision = state.get("human_decision", "approve")
    if isinstance(decision, str) and decision.startswith("investigate:"):
        return "prepare_investigation"   # ← new intermediate node
    return END


# ── Nodes ────────────────────────────────────────────────────────

def node_prepare_investigation(state: AgentState) -> dict:
    """Inject the human's investigation instruction into messages before retrying."""
    decision = state.get("human_decision", "")
    instruction = decision.removeprefix("investigate:").strip()
    logger.info("hitl_prepare_investigation instruction=%r", instruction[:80])
    inject_msg = {
        "role": "user",
        "content": [{
            "type": "text",
            "text": (
                "El desarrollador solicita investigar los siguientes puntos adicionales "
                f"y regenerar el JSON completo:\n{instruction}"
            ),
        }],
    }
    return {"messages": state["messages"] + [inject_msg]}


# ── Graph builder ────────────────────────────────────────────────

def build_graph(client, index: GlobalIndex):
    key = (id(client), id(index))
    if key in _graph_cache:
        return _graph_cache[key]

    graph = StateGraph(AgentState)

    graph.add_node("call_model",            partial(node_call_model, client=client))
    graph.add_node("execute_tools",         partial(node_execute_tools, index=index))
    graph.add_node("reflect",               partial(node_reflect, client=client))
    graph.add_node("human_review",          node_human_review)
    graph.add_node("prepare_investigation", node_prepare_investigation)

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
        {"prepare_investigation": "prepare_investigation", END: END},
    )
    graph.add_edge("prepare_investigation", "call_model")

    compiled = graph.compile(checkpointer=_checkpointer)
    _graph_cache[key] = compiled
    return compiled


# ── Helpers ──────────────────────────────────────────────────────

def _make_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _extract_result(final: dict | None) -> tuple:
    """Pull analysis, reflection, repos, files from final state dict."""
    if not isinstance(final, dict):
        return None, None, [], []
    return (
        final.get("analysis"),
        final.get("reflection"),
        list(final.get("repos_used", [])),
        final.get("files_used", []),
    )


# ── Public runners ───────────────────────────────────────────────

async def run_graph(
    query: AgentQuery,
    index: GlobalIndex,
    client,
    thread_id: str = "default",
) -> "GraphRunResult":
    graph = build_graph(client, index)
    config = _make_config(thread_id)

    initial_state: AgentState = {
        "messages": [{"role": "user", "content": build_initial_content(query)}],
        "query": query,
        "repos_used": [],
        "files_used": [],
        "tool_round": 0,
        "reflection_round": 0,
        "_stop_reason": "",
        "analysis": None,
        "reflection": None,
        "human_decision": None,
    }
    final = await graph.ainvoke(initial_state, config)

    snapshot = await graph.aget_state(config)
    interrupted = bool(snapshot and snapshot.next)
    interrupt_payload = None
    if interrupted and snapshot.tasks:
        for task in snapshot.tasks:
            if hasattr(task, "interrupts") and task.interrupts:
                interrupt_payload = task.interrupts[0].value
                break

    analysis, reflection, repos, files = _extract_result(final)
    return GraphRunResult(
        thread_id=thread_id,
        interrupted=interrupted,
        interrupt_payload=interrupt_payload,
        response=None if interrupted else AgentResponse(
            answer=analysis.markdown if analysis else "El agente no generó un análisis válido.",
            analysis=analysis,
            repos_consulted=repos,
            files_fetched=files,
            reflection_approved=reflection.approved if reflection else None,
            reflection_verdict=reflection.verdict if reflection else None,
        ),
    )


async def resume_graph(thread_id: str, decision: str, client, index: GlobalIndex) -> "GraphRunResult":
    """Resume an interrupted graph using Command(resume=...) — the documented LangGraph pattern."""
    graph = build_graph(client, index)
    config = _make_config(thread_id)

    final = await graph.ainvoke(Command(resume=decision), config)

    snapshot = await graph.aget_state(config)
    interrupted = bool(snapshot and snapshot.next)

    analysis, reflection, repos, files = _extract_result(final)
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


@dataclass
class GraphRunResult:
    thread_id: str
    interrupted: bool
    interrupt_payload: dict | None
    response: AgentResponse | None  # None only when interrupted=True
