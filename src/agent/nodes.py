import logging

from langgraph.types import interrupt

from src.agent.parsers import extract_analysis, serialize_content
from src.agent.prompt import SYSTEM_PROMPT
from src.agent.reflection import reflect
from src.agent.state import AgentState
from src.agent.tools import TOOLS, execute_tool
from src.core.config import settings
from src.models.index import GlobalIndex

logger = logging.getLogger(__name__)

_SYSTEM = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]

_FORCE_OUTPUT_MSG = (
    "Ya tienes suficiente contexto del código. "
    "Genera AHORA el JSON de RefinementAnalysis con toda la información que has recopilado. "
    "Responde únicamente con el bloque JSON, sin texto adicional."
)


async def node_call_model(state: AgentState, client) -> dict:
    """Call Claude and store only JSON-serializable data in state."""
    response = await client.messages.create(
        model=settings.claude_model,
        max_tokens=8192,
        system=_SYSTEM,
        tools=TOOLS,
        messages=state["messages"],
    )
    logger.info("node_call_model stop_reason=%s tool_round=%d", response.stop_reason, state["tool_round"])

    # Serialize content blocks to plain dicts — required for MemorySaver checkpointing
    serialized = serialize_content(response.content)
    return {
        "messages": state["messages"] + [{"role": "assistant", "content": serialized}],
        "_stop_reason": response.stop_reason,
    }


async def node_execute_tools(state: AgentState, index: GlobalIndex) -> dict:
    """Execute all tool calls from the last assistant message.

    index is injected via functools.partial in build_graph — not stored in state
    to keep AgentState fully JSON-serializable for all LangGraph checkpointers.
    """
    last_content = state["messages"][-1]["content"]  # already plain dicts
    tool_results = []
    repos_used = list(state["repos_used"])
    files_used = list(state["files_used"])

    for block in last_content:
        if block["type"] != "tool_use":
            continue

        logger.info("node_execute_tools tool=%s round=%d", block["name"], state["tool_round"] + 1)
        result = await execute_tool(block["name"], block["input"], index, state["query"].project)
        tool_results.append({"type": "tool_result", "tool_use_id": block["id"], "content": result})

        if "repo" in block["input"] and block["input"]["repo"] not in repos_used:
            repos_used.append(block["input"]["repo"])
        if block["name"] == "fetch_file":
            entry = f"{block['input'].get('repo')}:{block['input'].get('path')}"
            if entry not in files_used:
                files_used.append(entry)

    new_messages = state["messages"] + [{"role": "user", "content": tool_results}]
    new_tool_round = state["tool_round"] + 1

    if new_tool_round >= 10:
        logger.warning("node_execute_tools max_rounds=%d — forcing final output", new_tool_round)
        new_messages.append({"role": "user", "content": [{"type": "text", "text": _FORCE_OUTPUT_MSG}]})

    return {
        "messages": new_messages,
        "repos_used": repos_used,
        "files_used": files_used,
        "tool_round": new_tool_round,
    }


def node_human_review(state: AgentState) -> dict:
    """Pause the graph and wait for human approval via interrupt()."""
    analysis = state.get("analysis")
    reflection = state.get("reflection")

    payload = {
        "message": "El análisis requiere revisión. ¿Apruebas o quieres que investigue más?",
        "analysis_title": analysis.title if analysis else "Sin título",
        "analysis_summary": analysis.summary if analysis else "",
        "reflection_verdict": reflection.verdict if reflection else "",
        "gaps": reflection.gaps if reflection else [],
        "options": {
            "approve": "Aceptar el análisis tal como está",
            "investigate:<instrucción>": "Investigar puntos adicionales antes de finalizar",
        },
    }

    decision = interrupt(payload)
    logger.info("node_human_review decision=%r", decision)
    return {"human_decision": decision}


async def node_reflect(state: AgentState, client) -> dict:
    """Evaluate analysis quality using the last assistant message content."""
    last_content = state["messages"][-1]["content"]
    raw = next((b["text"] for b in last_content if b["type"] == "text"), "")
    analysis = extract_analysis(raw)

    if not analysis:
        logger.warning("node_reflect json_invalid — skipping reflection")
        return {"analysis": None, "reflection": None}

    if state["reflection_round"] >= 1:
        logger.info("node_reflect skipped (max reflection rounds reached)")
        return {"analysis": analysis, "reflection": None}

    reflection = await reflect(
        state["query"], analysis, state["repos_used"], state["files_used"],
        client, settings.claude_model
    )

    if not reflection.approved and reflection.gaps:
        gaps_text = "\n".join(f"- {g}" for g in reflection.gaps)
        retry_msg = {
            "role": "user",
            "content": [{
                "type": "text",
                "text": (
                    "El análisis necesita completarse. Investiga estos puntos adicionales "
                    "usando las herramientas disponibles y luego regenera el JSON completo:\n"
                    f"{gaps_text}"
                ),
            }],
        }
        return {
            "analysis": None,
            "reflection": reflection,
            "reflection_round": state["reflection_round"] + 1,
            "messages": state["messages"] + [retry_msg],
        }

    return {"analysis": analysis, "reflection": reflection}
