import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from src.core.config import settings
from src.indexer.azure_devops import AzureDevOpsClient
from src.indexer.crawler_dotnet import crawl_dotnet_repo
from src.indexer.crawler_python import crawl_python_repo
from src.indexer.embedder import index_repo_vectors
from src.models.index import GlobalIndex, RepoIndex
from src.retrieval.vector_store import ensure_collection

logger = logging.getLogger(__name__)

# Serializes all write operations so concurrent webhooks don't race.
index_lock = asyncio.Lock()

# In-memory cache: avoids repeated disk reads on every query.
_cache: GlobalIndex | None = None


async def _index_repo(client: AzureDevOpsClient, project: str, repo: dict) -> RepoIndex | None:
    try:
        tree = await client.get_full_tree(project, repo["id"])
        paths = [i["path"] for i in tree if i.get("gitObjectType") == "blob"]

        has_csproj = any(p.endswith(".csproj") for p in paths)
        if has_csproj:
            return await crawl_dotnet_repo(client, project, repo)
        return await crawl_python_repo(client, project, repo)
    except Exception as exc:
        logger.error("index_repo_failed repo=%s error=%s", repo["name"], exc)
        return None


async def build_index() -> GlobalIndex:
    client = AzureDevOpsClient()
    tasks = []
    repo_list = []

    for project in settings.azure_devops_projects:
        repos = await client.list_repos(project)
        for repo in repos:
            tasks.append(_index_repo(client, project, repo))
            repo_list.append(repo["name"])

    logger.info("index_start repos=%s", repo_list)
    results = await asyncio.gather(*tasks)

    repos: dict[str, RepoIndex] = {}
    for repo_index in results:
        if repo_index:
            repos[repo_index.name] = repo_index

    index = GlobalIndex(
        repos=repos,
        last_updated=datetime.now(timezone.utc).isoformat(),
    )

    async with index_lock:
        _persist(index)
        _set_cache(index)

    logger.info("index_persisted repos=%d path=%s", len(repos), settings.index_path)

    ensure_collection()
    for repo_index in index.repos.values():
        logger.info("vectorizing repo=%s", repo_index.name)
        index_repo_vectors(repo_index)

    logger.info("index_done repos=%d", len(repos))
    return index


def _persist(index: GlobalIndex) -> None:
    """Atomic write: write to .tmp then rename so readers never see a partial file."""
    tmp_path = settings.index_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(index.model_dump_json(indent=2))
    os.replace(tmp_path, settings.index_path)


def _set_cache(index: GlobalIndex) -> None:
    global _cache
    _cache = index


def _clear_cache() -> None:
    global _cache
    _cache = None


def load_index() -> GlobalIndex | None:
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(settings.index_path, encoding="utf-8") as f:
            _cache = GlobalIndex.model_validate(json.load(f))
        return _cache
    except FileNotFoundError:
        return None
