from src.models.index import GlobalIndex, RepoIndex

_FRONTEND_PATTERNS = (
    "-ui",
    "ui-kit",
    "frontend",
    "storefront",
    "storagefront",
    "mobile",
    "seller-portal",
    "-app",
)


def _is_frontend(repo_name: str) -> bool:
    name = repo_name.lower()
    return any(p in name for p in _FRONTEND_PATTERNS)


def search_repos(index: GlobalIndex, query: str, project: str | None = None) -> list[RepoIndex]:
    query_lower = query.lower()
    tokens = set(query_lower.split())

    scored: list[tuple[int, RepoIndex]] = []
    for repo in index.repos.values():
        if _is_frontend(repo.name):
            continue
        if project and repo.project != project:
            continue

        score = 0
        name_lower = repo.name.lower()

        # match en nombre del repo
        score += sum(2 for t in tokens if t in name_lower)

        # match en endpoints
        for ep in repo.endpoints:
            score += sum(1 for t in tokens if t in ep.path.lower() or t in ep.file.lower())

        # match en services y schemas
        for s in repo.services + repo.schemas:
            score += sum(1 for t in tokens if t in s.lower())

        # match en árbol de archivos
        for path in repo.tree:
            score += sum(1 for t in tokens if t in path.lower())

        if score > 0:
            scored.append((score, repo))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [repo for _, repo in scored[:5]]


def build_context_summary(repos: list[RepoIndex]) -> str:
    parts = []
    for repo in repos:
        endpoints_str = "\n".join(
            f"  [{ep.method}] {ep.path} → {ep.file}" for ep in repo.endpoints[:10]
        )
        services_str = ", ".join(repo.services[:10])
        schemas_str = ", ".join(repo.schemas[:10])
        tree_sample = "\n".join(f"  {p}" for p in repo.tree[:30])

        parts.append(
            f"## Repo: {repo.name} ({repo.project} / {repo.repo_type})\n"
            f"### Endpoints\n{endpoints_str or '  (ninguno detectado)'}\n"
            f"### Services\n  {services_str or '(ninguno)'}\n"
            f"### Schemas\n  {schemas_str or '(ninguno)'}\n"
            f"### Estructura (muestra)\n{tree_sample}\n"
        )

    return "\n---\n".join(parts)
