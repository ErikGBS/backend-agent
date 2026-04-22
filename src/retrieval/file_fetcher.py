from src.indexer.azure_devops import AzureDevOpsClient
from src.models.index import GlobalIndex, RepoIndex


async def fetch_relevant_files(
    index: GlobalIndex,
    repos: list[RepoIndex],
    query: str,
    max_files: int = 5,
) -> dict[str, str]:
    query_lower = query.lower()
    tokens = set(query_lower.split())
    client = AzureDevOpsClient()

    fetched: dict[str, str] = {}

    for repo in repos:
        # primero buscar en key_files ya indexados
        for path, content in repo.key_files.items():
            path_lower = path.lower()
            if any(t in path_lower for t in tokens):
                fetched[f"{repo.name}:{path}"] = content
                if len(fetched) >= max_files:
                    return fetched

        # si no alcanza, hacer fetch puntual desde Azure DevOps
        if len(fetched) < max_files:
            for path in repo.tree:
                path_lower = path.lower()
                if any(t in path_lower for t in tokens) and path not in repo.key_files:
                    try:
                        content = await client.get_file_content(repo.project, _repo_id(index, repo), path)
                        fetched[f"{repo.name}:{path}"] = content[:3000]
                    except Exception:
                        pass
                    if len(fetched) >= max_files:
                        return fetched

    return fetched


def _repo_id(index: GlobalIndex, repo: RepoIndex) -> str:
    # el id real del repo no se guarda en el índice actual; usar el nombre como fallback
    # en una mejora futura guardar el repo_id en RepoIndex
    return repo.name
