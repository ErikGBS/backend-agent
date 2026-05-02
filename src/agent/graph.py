import logging
from functools import partial

from langgraph.graph import END, StateGraph

from src.agent.nodes import node_call_model, node_execute_tools, node_reflect
from src.agent.state import AgentState
from src.models.index import GlobalIndex
from src.models.query import AgentQuery, AgentResponse

logger = logging.getLogger(__name__)


# ── Edge conditions ──────────────────────────────────────────────

def _after_model(state: AgentState) -> str:
    """Decide next node after Claude responds."""
    last_response = state.get("_last_response")
    if last_response is None:
        return "reflect"
    if last_response.stop_reason == "tool_use" and state["tool_round"] < 10:
        return "execute_tools"
    return "reflect"


def _after_reflect(state: AgentState) -> str:
    """If reflection found gaps and analysis is None, retry the model. Otherwise finish."""
    if state.get("analysis") is None and state.get("reflection") is not None:
        # Reflection injected gaps → retry call_model with enriched messages
        return "call_model"
    return END


# ── Graph builder ────────────────────────────────────────────────

def build_graph(client):
    """Build and compile the agent StateGraph with the given Anthropic client."""

    graph = StateGraph(AgentState)

    # Bind client to nodes that need it
    graph.add_node("call_model",    partial(node_call_model, client=client))
    graph.add_node("execute_tools", node_execute_tools)
    graph.add_node("reflect",       partial(node_reflect, client=client))

    graph.set_entry_point("call_model")

    graph.add_conditional_edges("call_model",    _after_model,   {"execute_tools": "execute_tools", "reflect": "reflect"})
    graph.add_edge("execute_tools", "call_model")
    graph.add_conditional_edges("reflect",       _after_reflect, {"call_model": "call_model", END: END})

    return graph.compile()


# ── Public runner ────────────────────────────────────────────────

async def run_graph(query: AgentQuery, index: GlobalIndex, client) -> AgentResponse:
    """Entry point — runs the compiled graph and returns AgentResponse."""
    from src.agent.core import _build_initial_content  # shared helper

    graph = build_graph(client)

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
        "_last_response": None,
    }

    final_state = await graph.ainvoke(initial_state)

    analysis = final_state.get("analysis")
    reflection = final_state.get("reflection")

    logger.info(
        "graph_done repos=%s reflection_approved=%s",
        ",".join(final_state.get("repos_used", [])) or "none",
        reflection.approved if reflection else "skipped",
    )

    return AgentResponse(
        answer=analysis.markdown if analysis else "El agente no generó un análisis válido.",
        analysis=analysis,
        repos_consulted=list(final_state.get("repos_used", [])),
        files_fetched=final_state.get("files_used", []),
        reflection_approved=reflection.approved if reflection else None,
        reflection_verdict=reflection.verdict if reflection else None,
    )
