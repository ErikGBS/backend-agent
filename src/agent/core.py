import json
import re

import anthropic

from src.agent.prompt import SYSTEM_PROMPT, build_user_message
from src.core.config import settings
from src.models.index import GlobalIndex
from src.models.query import AgentPlan, AgentQuery, AgentResponse
from src.retrieval.file_fetcher import fetch_relevant_files
from src.retrieval.index_store import build_context_summary, search_repos
from src.retrieval.vector_store import search as vector_search

_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_plan(raw: str) -> AgentPlan | None:
    text = _JSON_FENCE.sub("", raw).strip()
    try:
        data = json.loads(text)
        return AgentPlan.model_validate(data)
    except (json.JSONDecodeError, ValueError):
        return None


async def run_agent(query: AgentQuery, index: GlobalIndex) -> AgentResponse:
    hits = vector_search(query.query, top_k=8, project=query.project)

    if hits:
        repo_names = list({h["repo"] for h in hits})
        relevant_repos = [index.repos[r] for r in repo_names if r in index.repos]
        fetched_files = {f"{h['repo']}:{h['file_path']}": h.get("text", "") for h in hits}
    else:
        relevant_repos = search_repos(index, query.query, project=query.project)
        fetched_files = await fetch_relevant_files(relevant_repos, query.query)

    context = build_context_summary(relevant_repos)
    user_text = build_user_message(query.query, context, fetched_files)

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

    content.append({"type": "text", "text": user_text})

    async with _client.messages.stream(
        model=settings.claude_model,
        max_tokens=32000,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": content}],
    ) as stream:
        message = await stream.get_final_message()

    raw = message.content[0].text
    plan = _extract_plan(raw)
    answer_md = plan.markdown if plan else raw

    return AgentResponse(
        answer=answer_md,
        plan=plan,
        repos_consulted=[r.name for r in relevant_repos],
        files_fetched=list(fetched_files.keys()),
    )
