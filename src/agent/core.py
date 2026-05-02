import json
import logging
import os
import re
from collections.abc import AsyncGenerator

import anthropic
from langsmith import traceable
from langsmith.wrappers import wrap_anthropic

from src.agent.prompt import SYSTEM_PROMPT
from src.agent.reflection import ReflectionResult, reflect
from src.agent.tools import TOOLS, execute_tool
from src.core.config import settings
from src.models.index import GlobalIndex
from src.models.query import AgentQuery, AgentResponse, RefinementAnalysis

# Re-export LangGraph runner so the API layer can import from one place
from src.agent.graph import run_graph as run_graph  # noqa: F401

logger = logging.getLogger(__name__)

# Configure LangSmith env vars before wrapping the client
if settings.langsmith_tracing:
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    if settings.langsmith_api_key:
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key

_raw_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
_client = wrap_anthropic(_raw_client) if settings.langsmith_tracing else _raw_client

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_MAX_TOOL_ROUNDS = 10
_MAX_REFLECTION_ROUNDS = 1  # máximo 1 ciclo de reflection para no inflar costos
_FORCE_OUTPUT_MSG = (
    "Ya tienes suficiente contexto del código. "
    "Genera AHORA el JSON de RefinementAnalysis con toda la información que has recopilado. "
    "Responde únicamente con el bloque JSON, sin texto adicional."
)


def _extract_analysis(raw: str) -> RefinementAnalysis | None:
    text = _JSON_FENCE.sub("", raw).strip()
    try:
        data = json.loads(text)
        return RefinementAnalysis.model_validate(data)
    except (json.JSONDecodeError, ValueError):
        return None


def _build_initial_content(query: AgentQuery) -> list[dict]:
    content: list[dict] = []
    if query.image_base64:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": query.image_media_type or "image/jpeg",
                "data": query.image_base64,
            },
        })
    content.append({"type": "text", "text": f"## Consulta\n{query.query}"})
    return content


@traceable(name="backend-agent/run", tags=["agent"])
async def run_agent(query: AgentQuery, index: GlobalIndex) -> AgentResponse:
    query_preview = query.query[:80].replace("\n", " ")
    logger.info("query_start project=%s query=%r", query.project or "all", query_preview)

    messages: list[dict] = [{"role": "user", "content": _build_initial_content(query)}]
    repos_used: set[str] = set()
    files_used: list[str] = []
    response = None
    tool_round = 0
    reflection_round = 0

    while True:
        response = await _client.messages.create(
            model=settings.claude_model,
            max_tokens=8192,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            raw = next((b.text for b in response.content if b.type == "text"), "")
            analysis = _extract_analysis(raw)
            if not analysis:
                logger.warning("query_done rounds=%d json_valid=false raw_preview=%r", tool_round, raw[:120])
                return AgentResponse(
                    answer=raw,
                    analysis=None,
                    repos_consulted=list(repos_used),
                    files_fetched=files_used,
                )

            # Reflection: evalúa calidad y relanza si hay gaps concretos
            reflection: ReflectionResult | None = None
            if reflection_round < _MAX_REFLECTION_ROUNDS:
                reflection = await reflect(
                    query, analysis, list(repos_used), files_used, _raw_client, settings.claude_model
                )
                if not reflection.approved and reflection.gaps:
                    reflection_round += 1
                    gaps_text = "\n".join(f"- {g}" for g in reflection.gaps)
                    logger.info("reflection_retry round=%d gaps=%d", reflection_round, len(reflection.gaps))
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({
                        "role": "user",
                        "content": [{
                            "type": "text",
                            "text": (
                                "El análisis necesita completarse. Investiga estos puntos adicionales "
                                "usando las herramientas disponibles y luego regenera el JSON completo:\n"
                                f"{gaps_text}"
                            ),
                        }],
                    })
                    continue  # relanza el loop con el contexto enriquecido

            logger.info(
                "query_done rounds=%d reflection_approved=%s repos=%s",
                tool_round,
                reflection.approved if reflection else "skipped",
                ",".join(repos_used) or "none",
            )
            return AgentResponse(
                answer=analysis.markdown,
                analysis=analysis,
                repos_consulted=list(repos_used),
                files_fetched=files_used,
                reflection_approved=reflection.approved if reflection else None,
                reflection_verdict=reflection.verdict if reflection else None,
            )

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in response.content:
                if block.type == "tool_use":
                    inputs_preview = {
                        k: (v[:60] if isinstance(v, str) else v)
                        for k, v in block.input.items()
                    }
                    logger.info("tool_call round=%d tool=%s inputs=%s", tool_round + 1, block.name, inputs_preview)

                    result = await execute_tool(block.name, block.input, index, query.project)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
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
                    model=settings.claude_model,
                    max_tokens=8192,
                    system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                    messages=messages,
                )
                raw = next((b.text for b in final.content if hasattr(b, "text")), "")
                analysis = _extract_analysis(raw)
                if not analysis:
                    logger.warning("forced_output json_valid=false raw_preview=%r", raw[:120])
                return AgentResponse(
                    answer=analysis.markdown if analysis else raw,
                    analysis=analysis,
                    repos_consulted=list(repos_used),
                    files_fetched=files_used,
                )

            messages.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop reason (e.g. max_tokens)
        logger.warning("unexpected_stop stop_reason=%s rounds=%d", response.stop_reason, tool_round)
        raw = next((b.text for b in response.content if hasattr(b, "text")), "")
        analysis = _extract_analysis(raw)
        return AgentResponse(
            answer=analysis.markdown if analysis else raw,
            analysis=analysis,
            repos_consulted=list(repos_used),
            files_fetched=files_used,
        )


def _sse(event: str, **data) -> str:
    return f"data: {json.dumps({'event': event, **data})}\n\n"


_TOOL_LABELS = {
    "search_code": "Buscando en el código",
    "fetch_file": "Leyendo archivo",
    "list_endpoints": "Listando endpoints",
    "get_schema": "Obteniendo schema",
}


@traceable(name="backend-agent/stream", tags=["agent", "stream"])
async def run_agent_stream(
    query: AgentQuery, index: GlobalIndex
) -> AsyncGenerator[str, None]:
    """Same agent loop as run_agent but yields SSE events for real-time feedback."""
    messages: list[dict] = [{"role": "user", "content": _build_initial_content(query)}]
    repos_used: set[str] = set()
    files_used: list[str] = []
    tool_round = 0

    yield _sse("start", message="Analizando historia de usuario...")

    while True:
        response = await _raw_client.messages.create(
            model=settings.claude_model,
            max_tokens=8192,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            raw = next((b.text for b in response.content if b.type == "text"), "")
            analysis = _extract_analysis(raw)

            # Stream the final markdown token by token
            text = analysis.markdown if analysis else raw
            chunk_size = 80
            for i in range(0, len(text), chunk_size):
                yield _sse("token", text=text[i : i + chunk_size])

            yield _sse(
                "done",
                repos=list(repos_used),
                files=files_used,
                rounds=tool_round,
                valid_json=analysis is not None,
            )
            return

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue

                label = _TOOL_LABELS.get(block.name, block.name)
                detail = next(iter(block.input.values()), "") if block.input else ""
                yield _sse(
                    "progress",
                    round=tool_round + 1,
                    tool=block.name,
                    label=label,
                    detail=str(detail)[:80],
                )
                logger.info("stream_tool round=%d tool=%s", tool_round + 1, block.name)

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
                yield _sse("progress", round=tool_round, tool="force_output", label="Generando análisis final", detail="")
                final = await _raw_client.messages.create(
                    model=settings.claude_model,
                    max_tokens=8192,
                    system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                    messages=messages,
                )
                raw = next((b.text for b in final.content if hasattr(b, "text")), "")
                analysis = _extract_analysis(raw)
                text = analysis.markdown if analysis else raw
                chunk_size = 80
                for i in range(0, len(text), chunk_size):
                    yield _sse("token", text=text[i : i + chunk_size])
                yield _sse("done", repos=list(repos_used), files=files_used, rounds=tool_round, valid_json=analysis is not None)
                return

            messages.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop
        raw = next((b.text for b in response.content if hasattr(b, "text")), "")
        analysis = _extract_analysis(raw)
        text = analysis.markdown if analysis else raw
        for i in range(0, len(text), 80):
            yield _sse("token", text=text[i : i + 80])
        yield _sse("done", repos=list(repos_used), files=files_used, rounds=tool_round, valid_json=False)
        return
