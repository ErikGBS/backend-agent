import asyncio
import json
from datetime import datetime, timezone

from src.core.config import settings
from src.indexer.azure_devops import AzureDevOpsClient
from src.indexer.crawler_dotnet import crawl_dotnet_repo
from src.indexer.crawler_python import crawl_python_repo
from src.models.index import GlobalIndex, RepoIndex

_PYTHON_KEYWORDS = ("python", "api", "bff", "functions")
_DOTNET_KEYWORDS = (".net", "dotnet", "csharp", "functions")


def _detect_repo_type(repo_name: str, default_project: str) -> str:
    name_lower = repo_name.lower()
    if any(k in name_lower for k in ("functions", "api", "bff")):
        if default_project == "Cantera":
            return "python"
    return "python"  # default; se refina con detección de archivos


async def _index_repo(client: AzureDevOpsClient, project: str, repo: dict) -> RepoIndex | None:
    try:
        tree = await client.get_full_tree(project, repo["id"])
        paths = [i["path"] for i in tree if i.get("gitObjectType") == "blob"]

        has_csproj = any(p.endswith(".csproj") for p in paths)
        if has_csproj:
            return await crawl_dotnet_repo(client, project, repo)
        return await crawl_python_repo(client, project, repo)
    except Exception as exc:
        print(f"[indexer] error en {repo['name']}: {exc}")
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

    results = await asyncio.gather(*tasks)

    repos: dict[str, RepoIndex] = {}
    for repo_index in results:
        if repo_index:
            repos[repo_index.name] = repo_index

    index = GlobalIndex(
        repos=repos,
        last_updated=datetime.now(timezone.utc).isoformat(),
    )
    _persist(index)
    return index


def _persist(index: GlobalIndex) -> None:
    with open(settings.index_path, "w", encoding="utf-8") as f:
        f.write(index.model_dump_json(indent=2))


def load_index() -> GlobalIndex | None:
    try:
        with open(settings.index_path, encoding="utf-8") as f:
            return GlobalIndex.model_validate(json.load(f))
    except FileNotFoundError:
        return None
