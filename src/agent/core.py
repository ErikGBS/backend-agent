import json
import re

import anthropic

from src.agent.prompt import SYSTEM_PROMPT
from src.agent.tools import TOOLS, execute_tool
from src.core.config import settings
from src.models.index import GlobalIndex
from src.models.query import AgentPlan, AgentQuery, AgentResponse

_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_MAX_ITERATIONS = 8


def _extract_plan(raw: str) -> AgentPlan | None:
    text = _JSON_FENCE.sub("", raw).strip()
    try:
        data = json.loads(text)
        return AgentPlan.model_validate(data)
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

    for _ in range(_MAX_ITERATIONS):
        response = await _client.messages.create(
            model=settings.claude_model,
            max_tokens=32000,
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
            plan = _extract_plan(raw)
            return AgentResponse(
                answer=plan.markdown if plan else raw,
                plan=plan,
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

            messages.append({"role": "user", "content": tool_results})

    # agotadas las iteraciones — devolver lo último disponible
    last_text = ""
    if response:
        last_text = next((b.text for b in response.content if hasattr(b, "text")), "")
    plan = _extract_plan(last_text)
    return AgentResponse(
        answer=plan.markdown if plan else last_text,
        plan=plan,
        repos_consulted=list(repos_used),
        files_fetched=files_used,
    )
