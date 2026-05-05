import json
import re

from src.models.query import AgentQuery, RefinementAnalysis

_FENCE_BLOCK = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_OBJECT_SPAN = re.compile(r"\{.*\}", re.DOTALL)


def extract_analysis(raw: str) -> RefinementAnalysis | None:
    candidates: list[str] = []

    fence_match = _FENCE_BLOCK.search(raw)
    if fence_match:
        candidates.append(fence_match.group(1))

    candidates.append(raw.strip())

    object_match = _OBJECT_SPAN.search(raw)
    if object_match:
        candidates.append(object_match.group(0))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            return RefinementAnalysis.model_validate(data)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def build_initial_content(query: AgentQuery) -> list[dict]:
    """Build the initial user message content from a query."""
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


def serialize_content(content) -> list[dict]:
    """Convert Anthropic response content blocks to JSON-serializable dicts."""
    result = []
    for block in content:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
    return result
