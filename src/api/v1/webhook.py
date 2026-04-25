import asyncio

from fastapi import APIRouter, BackgroundTasks, Request

from src.indexer.azure_devops import AzureDevOpsClient
from src.indexer.crawler_dotnet import crawl_dotnet_repo
from src.indexer.crawler_python import crawl_python_repo
from src.indexer.embedder import index_repo_vectors
from src.indexer.index_builder import _persist, load_index
from src.retrieval.vector_store import delete_repo_chunks, ensure_collection

router = APIRouter(prefix="/webhook", tags=["webhook"])


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

        # Keep both layers in sync: persist JSON index first, then update Qdrant.
        # Delete stale chunks before upserting so removed files don't linger.
        index.repos[repo_name] = repo_index
        _persist(index)

        ensure_collection()
        delete_repo_chunks(repo_name)
        index_repo_vectors(repo_index)

        print(f"[webhook] re-indexado: {repo_name}")
    except Exception as exc:
        print(f"[webhook] error re-indexando {repo_name}: {exc}")


@router.post("/azure-devops")
async def azure_devops_hook(request: Request, background_tasks: BackgroundTasks) -> dict:
    payload = await request.json()

    resource = payload.get("resource", {})
    repo_info = resource.get("repository", {})
    repo_id = repo_info.get("id", "")
    repo_name = repo_info.get("name", "")
    project = repo_info.get("project", {}).get("name", "")

    if repo_id and repo_name and project:
        background_tasks.add_task(_reindex_repo, project, repo_name, repo_id)

    return {"status": "accepted"}
