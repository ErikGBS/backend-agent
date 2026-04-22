import anthropic

from src.agent.prompt import SYSTEM_PROMPT, build_user_message
from src.core.config import settings
from src.models.index import GlobalIndex
from src.models.query import AgentQuery, AgentResponse
from src.retrieval.file_fetcher import fetch_relevant_files
from src.retrieval.index_store import build_context_summary, search_repos

_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


async def run_agent(query: AgentQuery, index: GlobalIndex) -> AgentResponse:
    relevant_repos = search_repos(index, query.query, project=query.project)
    fetched_files = await fetch_relevant_files(index, relevant_repos, query.query)

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

    response = await _client.messages.create(
        model=settings.claude_model,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": content}],
    )

    answer = response.content[0].text

    return AgentResponse(
        answer=answer,
        repos_consulted=[r.name for r in relevant_repos],
        files_fetched=list(fetched_files.keys()),
    )
