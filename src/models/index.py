from pydantic import BaseModel


class EndpointInfo(BaseModel):
    path: str
    method: str
    file: str


class RepoIndex(BaseModel):
    name: str
    repo_id: str
    project: str
    repo_type: str  # "python" | "dotnet"
    tree: list[str]
    endpoints: list[EndpointInfo]
    services: list[str]
    schemas: list[str]
    key_files: dict[str, str]  # path -> content (solo archivos relevantes)


class GlobalIndex(BaseModel):
    repos: dict[str, RepoIndex]
    last_updated: str
