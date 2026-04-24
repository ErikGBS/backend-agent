import re

from src.indexer.azure_devops import AzureDevOpsClient
from src.models.index import EndpointInfo, RepoIndex

_KEY_EXTENSIONS = {".csproj", ".json", ".cs"}
_KEY_FILES = {"host.json", "local.settings.json", "appsettings.json"}
_KEY_DIRS = {"controllers", "functions", "handlers", "services", "models", "dtos"}
_HTTP_TRIGGER_RE = re.compile(r'\[HttpTrigger\(.*?Route\s*=\s*"([^"]+)"', re.IGNORECASE | re.DOTALL)
_ROUTE_RE = re.compile(r'\[Route\("([^"]+)"\)', re.IGNORECASE)
_CLASS_RE = re.compile(r'public\s+(?:class|interface)\s+(\w+)', re.MULTILINE)


def _is_key_file(path: str) -> bool:
    filename = path.split("/")[-1]
    ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""
    if filename in _KEY_FILES:
        return True
    parts = set(path.lower().split("/"))
    return ext in _KEY_EXTENSIONS and bool(parts & _KEY_DIRS)


def _extract_endpoints(content: str, file_path: str) -> list[EndpointInfo]:
    endpoints = []
    for match in _HTTP_TRIGGER_RE.finditer(content):
        endpoints.append(EndpointInfo(method="FUNCTION", path=match.group(1), file=file_path))
    for match in _ROUTE_RE.finditer(content):
        endpoints.append(EndpointInfo(method="HTTP", path=match.group(1), file=file_path))
    return endpoints


def _extract_classes(content: str) -> list[str]:
    return _CLASS_RE.findall(content)


async def crawl_dotnet_repo(
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
        key_files[path] = content[:3000]

        if path.endswith(".cs"):
            endpoints.extend(_extract_endpoints(content, path))
            classes = _extract_classes(content)
            if any(p in path.lower() for p in ["service"]):
                services.extend(classes)
            elif any(p in path.lower() for p in ["model", "dto", "response", "request"]):
                schemas.extend(classes)

    return RepoIndex(
        name=repo_name,
        repo_id=repo_id,
        project=project,
        repo_type="dotnet",
        tree=all_paths,
        endpoints=endpoints,
        services=list(set(services)),
        schemas=list(set(schemas)),
        key_files=key_files,
    )
