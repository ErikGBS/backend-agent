import json
import re

import anthropic

from src.agent.prompt import SYSTEM_PROMPT
from src.agent.tools import TOOLS, execute_tool
from src.core.config import settings
from src.models.index import GlobalIndex
from src.models.query import AgentQuery, AgentResponse, RefinementAnalysis

_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_MAX_TOOL_ROUNDS = 10
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


async def run_agent(query: AgentQuery, index: GlobalIndex) -> AgentResponse:
    messages: list[dict] = [{"role": "user", "content": _build_initial_content(query)}]
    repos_used: set[str] = set()
    files_used: list[str] = []
    response = None
    tool_round = 0

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
            return AgentResponse(
                answer=analysis.markdown if analysis else raw,
                analysis=analysis,
                repos_consulted=list(repos_used),
                files_fetched=files_used,
            )

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in response.content:
                if block.type == "tool_use":
                    result = await execute_tool(block.name, block.input, index)
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
                # Force the model to produce the final JSON on the next call
                messages.append({"role": "user", "content": tool_results})
                messages.append({"role": "user", "content": [{"type": "text", "text": _FORCE_OUTPUT_MSG}]})
                # One final call without tools to get the JSON
                final = await _client.messages.create(
                    model=settings.claude_model,
                    max_tokens=8192,
                    system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                    messages=messages,
                )
                raw = next((b.text for b in final.content if hasattr(b, "text")), "")
                analysis = _extract_analysis(raw)
                return AgentResponse(
                    answer=analysis.markdown if analysis else raw,
                    analysis=analysis,
                    repos_consulted=list(repos_used),
                    files_fetched=files_used,
                )

            messages.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop reason (e.g. max_tokens) — return what we have
        raw = next((b.text for b in response.content if hasattr(b, "text")), "")
        analysis = _extract_analysis(raw)
        return AgentResponse(
            answer=analysis.markdown if analysis else raw,
            analysis=analysis,
            repos_consulted=list(repos_used),
            files_fetched=files_used,
        )
