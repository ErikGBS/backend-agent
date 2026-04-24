import re

from src.indexer.azure_devops import AzureDevOpsClient
from src.models.index import EndpointInfo, RepoIndex

_KEY_FILES = {
    "pyproject.toml", "requirements.txt", "requirements-dev.txt",
    "host.json", "local.settings.json",
}
_KEY_DIRS = {"api", "v1", "services", "schemas", "repositories", "application", "infrastructure"}
_ENDPOINT_RE = re.compile(r'@router\.(get|post|put|patch|delete)\(["\']([^"\']+)["\']', re.IGNORECASE)
_CLASS_RE = re.compile(r'^class\s+(\w+)', re.MULTILINE)


def _is_key_file(path: str) -> bool:
    filename = path.split("/")[-1]
    if filename in _KEY_FILES:
        return True
    parts = set(path.lower().split("/"))
    return bool(parts & _KEY_DIRS) and filename.endswith(".py")


def _extract_endpoints(content: str, file_path: str) -> list[EndpointInfo]:
    endpoints = []
    for match in _ENDPOINT_RE.finditer(content):
        endpoints.append(EndpointInfo(method=match.group(1).upper(), path=match.group(2), file=file_path))
    return endpoints


def _extract_classes(content: str) -> list[str]:
    return _CLASS_RE.findall(content)


async def crawl_python_repo(
    client: AzureDevOpsClient,
    project: str,
    repo: dict,
) -> RepoIndex:
    repo_id = repo["id"]
    repo_name = repo["name"]

    tree_items = await client.get_full_tree(project, repo_id)
    all_paths = [item["path"] for item in tree_items if item.get("gitObjectType") == "blob"]

    key_files: dict[str, str] = {}
    endpoints: list[EndpointInfo] = []
    services: list[str] = []
    schemas: list[str] = []

    for path in all_paths:
        if not _is_key_file(path):
            continue
        content = await client.get_file_content(project, repo_id, path)
        key_files[path] = content[:3000]  # cap para no saturar el índice

        if path.endswith(".py"):
            endpoints.extend(_extract_endpoints(content, path))
            classes = _extract_classes(content)
            if any(p in path.lower() for p in ["service"]):
                services.extend(classes)
            elif any(p in path.lower() for p in ["schema", "model", "dto"]):
                schemas.extend(classes)

    return RepoIndex(
        name=repo_name,
        repo_id=repo_id,
        project=project,
        repo_type="python",
        tree=all_paths,
        endpoints=endpoints,
        services=list(set(services)),
        schemas=list(set(schemas)),
        key_files=key_files,
    )
