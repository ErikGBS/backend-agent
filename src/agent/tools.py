from src.indexer.azure_devops import AzureDevOpsClient
from src.models.index import GlobalIndex
from src.retrieval.reranker import rerank
from src.retrieval.vector_store import search as vector_search

TOOLS = [
    {
        "name": "search_code",
        "description": (
            "Busca fragmentos de código semánticamente relacionados con la consulta. "
            "Si el usuario seleccionó un proyecto, el sistema ya filtra automáticamente "
            "los resultados a ese proyecto: NO menciones ni propongas repos que no "
            "aparezcan en los resultados de las herramientas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Consulta en lenguaje natural sobre el código a buscar",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_file",
        "description": (
            "Obtiene el contenido completo de un archivo específico. "
            "El sistema infiere el proyecto a partir del repo, no necesitas pasarlo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Nombre del repositorio"},
                "path": {
                    "type": "string",
                    "description": "Path del archivo (ej: /app/application/services/quotation_service.py)",
                },
            },
            "required": ["repo", "path"],
        },
    },
    {
        "name": "list_endpoints",
        "description": "Lista todos los endpoints HTTP detectados en un repositorio.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Nombre del repositorio"},
            },
            "required": ["repo"],
        },
    },
    {
        "name": "get_schema",
        "description": "Busca la definición de un schema Pydantic, clase, DTO o modelo ORM por nombre.",
        "input_schema": {
            "type": "object",
            "properties": {
                "class_name": {
                    "type": "string",
                    "description": "Nombre de la clase o schema a buscar",
                },
            },
            "required": ["class_name"],
        },
    },
]


async def execute_tool(
    name: str,
    inputs: dict,
    index: GlobalIndex,
    user_project: str | None = None,
) -> str:
    if name == "search_code":
        project = user_project
        # Fetch 2x candidates so the reranker has room to re-order
        hits = vector_search(inputs["query"], top_k=12, project=project)
        if not hits:
            return "No se encontraron resultados para esa búsqueda."
        hits = rerank(inputs["query"], hits, top_n=6)
        parts = [
            f"### {h['repo']}:{h['file_path']} (relevance: {h.get('rerank_score', 0):.3f})\n```\n{h.get('text', '')}\n```"
            for h in hits
        ]
        return "\n\n".join(parts)

    if name == "fetch_file":
        repo = index.repos.get(inputs["repo"])
        project = user_project or (repo.project if repo else None)
        if not project:
            return (
                f"Repo '{inputs['repo']}' no está en el índice y no hay proyecto seleccionado. "
                f"Disponibles: {', '.join(index.repos.keys())}"
            )
        client = AzureDevOpsClient()
        try:
            repo_id = repo.repo_id if repo else inputs["repo"]
            content = await client.get_file_content(project, repo_id, inputs["path"])
            return f"### {inputs['repo']}:{inputs['path']}\n```\n{content[:5000]}\n```"
        except Exception as exc:
            return f"Error al obtener el archivo: {exc}"

    if name == "list_endpoints":
        repo = index.repos.get(inputs["repo"])
        if not repo:
            available = ", ".join(index.repos.keys())
            return f"Repo '{inputs['repo']}' no encontrado. Disponibles: {available}"
        if not repo.endpoints:
            return "No se encontraron endpoints en este repo."
        return "\n".join(f"[{ep.method}] {ep.path} → {ep.file}" for ep in repo.endpoints)

    if name == "get_schema":
        class_lower = inputs["class_name"].lower()
        project = user_project
        matches = []
        for repo in index.repos.values():
            if project and repo.project != project:
                continue
            for item in repo.schemas + repo.services:
                if class_lower in item.lower():
                    matches.append(f"{repo.name}: {item}")
        if matches:
            return "\n".join(matches)
        hits = vector_search(f"class {inputs['class_name']}", top_k=3, project=project)
        if hits:
            parts = [
                f"### {h['repo']}:{h['file_path']}\n```\n{h.get('text', '')}\n```"
                for h in hits
            ]
            return "\n\n".join(parts)
        return f"No se encontró la clase '{inputs['class_name']}'."

    return f"Tool '{name}' no reconocida."
