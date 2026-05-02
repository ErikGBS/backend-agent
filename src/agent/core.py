import json
import logging
import os
from collections.abc import AsyncGenerator

import anthropic
from langsmith import traceable
from langsmith.wrappers import wrap_anthropic

from src.agent.parsers import build_initial_content, extract_analysis, serialize_content
from src.agent.prompt import SYSTEM_PROMPT
from src.agent.reflection import ReflectionResult, reflect
from src.agent.tools import TOOLS, execute_tool
from src.core.config import settings
from src.models.index import GlobalIndex
from src.models.query import AgentQuery, AgentResponse, RefinementAnalysis

logger = logging.getLogger(__name__)

# Configure LangSmith env vars before wrapping the client
if settings.langsmith_tracing:
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    if settings.langsmith_api_key:
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key

_raw_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
_client = wrap_anthropic(_raw_client) if settings.langsmith_tracing else _raw_client

_MAX_TOOL_ROUNDS = 10
_MAX_REFLECTION_ROUNDS = 1
_FORCE_OUTPUT_MSG = (
    "Ya tienes suficiente contexto del código. "
    "Genera AHORA el JSON de RefinementAnalysis con toda la información que has recopilado. "
    "Responde únicamente con el bloque JSON, sin texto adicional."
)

_SYSTEM = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]

_TOOL_LABELS = {
    "search_code": "Buscando en el código",
    "fetch_file": "Leyendo archivo",
    "list_endpoints": "Listando endpoints",
    "get_schema": "Obteniendo schema",
}


def _sse(event: str, **data) -> str:
    return f"data: {json.dumps({'event': event, **data})}\n\n"


@traceable(name="backend-agent/run", tags=["agent"])
async def run_agent(query: AgentQuery, index: GlobalIndex) -> AgentResponse:
    query_preview = query.query[:80].replace("\n", " ")
    logger.info("query_start project=%s query=%r", query.project or "all", query_preview)

    messages: list[dict] = [{"role": "user", "content": build_initial_content(query)}]
    repos_used: set[str] = set()
    files_used: list[str] = []
    tool_round = 0
    reflection_round = 0

    while True:
        response = await _client.messages.create(
            model=settings.claude_model,
            max_tokens=8192,
            system=_SYSTEM,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            raw = next((b.text for b in response.content if b.type == "text"), "")
            analysis = extract_analysis(raw)
            if not analysis:
                logger.warning("query_done rounds=%d json_valid=false raw_preview=%r", tool_round, raw[:120])
                return AgentResponse(
                    answer=raw, analysis=None,
                    repos_consulted=list(repos_used), files_fetched=files_used,
                )

            reflection: ReflectionResult | None = None
            if reflection_round < _MAX_REFLECTION_ROUNDS:
                reflection = await reflect(
                    query, analysis, list(repos_used), files_used, _raw_client, settings.claude_model
                )
                if not reflection.approved and reflection.gaps:
                    reflection_round += 1
                    gaps_text = "\n".join(f"- {g}" for g in reflection.gaps)
                    logger.info("reflection_retry round=%d gaps=%d", reflection_round, len(reflection.gaps))
                    messages.append({"role": "assistant", "content": serialize_content(response.content)})
                    messages.append({"role": "user", "content": [{"type": "text", "text": (
                        "El análisis necesita completarse. Investiga estos puntos adicionales "
                        f"usando las herramientas disponibles y luego regenera el JSON completo:\n{gaps_text}"
                    )}]})
                    continue

            logger.info("query_done rounds=%d reflection_approved=%s repos=%s",
                        tool_round, reflection.approved if reflection else "skipped",
                        ",".join(repos_used) or "none")
            return AgentResponse(
                answer=analysis.markdown, analysis=analysis,
                repos_consulted=list(repos_used), files_fetched=files_used,
                reflection_approved=reflection.approved if reflection else None,
                reflection_verdict=reflection.verdict if reflection else None,
            )

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": serialize_content(response.content)})
            tool_results = []

            for block in response.content:
                if block.type == "tool_use":
                    logger.info("tool_call round=%d tool=%s", tool_round + 1, block.name)
                    result = await execute_tool(block.name, block.input, index, query.project)
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
                    if "repo" in block.input:
                        repos_used.add(block.input["repo"])
                    if block.name == "fetch_file":
                        files_used.append(f"{block.input.get('repo')}:{block.input.get('path')}")

            tool_round += 1
            if tool_round >= _MAX_TOOL_ROUNDS:
                logger.warning("max_tool_rounds reached rounds=%d — forcing final output", tool_round)
                messages.append({"role": "user", "content": tool_results})
                messages.append({"role": "user", "content": [{"type": "text", "text": _FORCE_OUTPUT_MSG}]})
                final = await _client.messages.create(
                    model=settings.claude_model, max_tokens=8192, system=_SYSTEM, messages=messages,
                )
                raw = next((b.text for b in final.content if hasattr(b, "text")), "")
                analysis = extract_analysis(raw)
                return AgentResponse(
                    answer=analysis.markdown if analysis else raw, analysis=analysis,
                    repos_consulted=list(repos_used), files_fetched=files_used,
                )

            messages.append({"role": "user", "content": tool_results})
            continue

        logger.warning("unexpected_stop stop_reason=%s rounds=%d", response.stop_reason, tool_round)
        raw = next((b.text for b in response.content if hasattr(b, "text")), "")
        analysis = extract_analysis(raw)
        return AgentResponse(
            answer=analysis.markdown if analysis else raw, analysis=analysis,
            repos_consulted=list(repos_used), files_fetched=files_used,
        )


@traceable(name="backend-agent/stream", tags=["agent", "stream"])
async def run_agent_stream(query: AgentQuery, index: GlobalIndex) -> AsyncGenerator[str, None]:
    messages: list[dict] = [{"role": "user", "content": build_initial_content(query)}]
    repos_used: set[str] = set()
    files_used: list[str] = []
    tool_round = 0

    yield _sse("start", message="Analizando historia de usuario...")

    while True:
        response = await _raw_client.messages.create(
            model=settings.claude_model, max_tokens=8192, system=_SYSTEM, tools=TOOLS, messages=messages,
        )

        if response.stop_reason == "end_turn":
            raw = next((b.text for b in response.content if b.type == "text"), "")
            analysis = extract_analysis(raw)
            text = analysis.markdown if analysis else raw
            for i in range(0, len(text), 80):
                yield _sse("token", text=text[i: i + 80])
            yield _sse("done", repos=list(repos_used), files=files_used,
                       rounds=tool_round, valid_json=analysis is not None)
            return

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": serialize_content(response.content)})
            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue
                yield _sse("progress", round=tool_round + 1, tool=block.name,
                           label=_TOOL_LABELS.get(block.name, block.name),
                           detail=str(next(iter(block.input.values()), ""))[:80])
                result = await execute_tool(block.name, block.input, index, query.project)
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
                if "repo" in block.input:
                    repos_used.add(block.input["repo"])
                if block.name == "fetch_file":
                    files_used.append(f"{block.input.get('repo')}:{block.input.get('path')}")

            tool_round += 1
            if tool_round >= _MAX_TOOL_ROUNDS:
                messages.append({"role": "user", "content": tool_results})
                messages.append({"role": "user", "content": [{"type": "text", "text": _FORCE_OUTPUT_MSG}]})
                yield _sse("progress", round=tool_round, tool="force_output",
                           label="Generando análisis final", detail="")
                final = await _raw_client.messages.create(
                    model=settings.claude_model, max_tokens=8192, system=_SYSTEM, messages=messages,
                )
                raw = next((b.text for b in final.content if hasattr(b, "text")), "")
                analysis = extract_analysis(raw)
                text = analysis.markdown if analysis else raw
                for i in range(0, len(text), 80):
                    yield _sse("token", text=text[i: i + 80])
                yield _sse("done", repos=list(repos_used), files=files_used,
                           rounds=tool_round, valid_json=analysis is not None)
                return

            messages.append({"role": "user", "content": tool_results})
            continue

        raw = next((b.text for b in response.content if hasattr(b, "text")), "")
        analysis = extract_analysis(raw)
        text = analysis.markdown if analysis else raw
        for i in range(0, len(text), 80):
            yield _sse("token", text=text[i: i + 80])
        yield _sse("done", repos=list(repos_used), files=files_used, rounds=tool_round, valid_json=False)
        return
