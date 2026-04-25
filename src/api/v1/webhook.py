import asyncio
import base64
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

logger = logging.getLogger(__name__)

from src.core.config import settings
from src.indexer.azure_devops import AzureDevOpsClient
from src.indexer.crawler_dotnet import crawl_dotnet_repo
from src.indexer.crawler_python import crawl_python_repo
from src.indexer.embedder import index_repo_vectors
from src.indexer.index_builder import _persist, _set_cache, index_lock, load_index
from src.retrieval.vector_store import delete_repo_chunks, ensure_collection

router = APIRouter(prefix="/webhook", tags=["webhook"])


def _verify_basic_auth(authorization: str | None) -> None:
    """Validate Basic Auth header against WEBHOOK_SECRET. No-op if secret not configured."""
    if not settings.webhook_secret:
        return
    expected = base64.b64encode(settings.webhook_secret.encode()).decode()
    if not authorization or not authorization.startswith("Basic "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    if authorization.removeprefix("Basic ").strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _reindex_repo(project: str, repo_name: str, repo_id: str) -> None:
    client = AzureDevOpsClient()
    index = load_index()
    if not index:
        return

    try:
        repo = {"id": repo_id, "name": repo_name}
        tree = await client.get_full_tree(project, repo_id)
        paths = [i["path"] for i in tree if i.get("gitObjectType") == "blob"]
        has_csproj = any(p.endswith(".csproj") for p in paths)

        if has_csproj:
            repo_index = await crawl_dotnet_repo(client, project, repo)
        else:
            repo_index = await crawl_python_repo(client, project, repo)

        # Acquire lock so concurrent webhooks don't race on the index file.
        async with index_lock:
            index.repos[repo_name] = repo_index
            _persist(index)
            _set_cache(index)

        ensure_collection()
        delete_repo_chunks(repo_name)
        index_repo_vectors(repo_index)

        logger.info("reindex_done repo=%s project=%s", repo_name, project)
    except Exception as exc:
        logger.error("reindex_failed repo=%s project=%s error=%s", repo_name, project, exc)


@router.post("/azure-devops")
async def azure_devops_hook(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
) -> dict:
    _verify_basic_auth(authorization)
    payload = await request.json()

    resource = payload.get("resource", {})
    repo_info = resource.get("repository", {})
    repo_id = repo_info.get("id", "")
    repo_name = repo_info.get("name", "")
    project = repo_info.get("project", {}).get("name", "")

    if repo_id and repo_name and project:
        logger.info("reindex_triggered repo=%s project=%s", repo_name, project)
        background_tasks.add_task(_reindex_repo, project, repo_name, repo_id)

    return {"status": "accepted"}
