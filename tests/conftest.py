import os

# Set env vars before any src.* import so Settings() doesn't fail
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("AZURE_DEVOPS_ORG", "test-org")
os.environ.setdefault("AZURE_DEVOPS_PAT", "test-pat")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("API_KEY", "test-api-key")

import pytest

from src.models.index import EndpointInfo, GlobalIndex, RepoIndex


@pytest.fixture
def sample_repo() -> RepoIndex:
    return RepoIndex(
        name="maestro-bff-api",
        repo_id="repo-123",
        project="Cantera",
        repo_type="python",
        tree=["app/api/v1/candidates.py", "app/application/services/candidate_service.py"],
        endpoints=[
            EndpointInfo(path="/api/v1/candidates", method="GET", file="app/api/v1/candidates.py"),
            EndpointInfo(path="/api/v1/candidates/{id}", method="DELETE", file="app/api/v1/candidates.py"),
        ],
        services=["CandidateService", "QuotationService"],
        schemas=["CandidateSchema", "CandidateResponse", "QuotationSummary"],
        key_files={},
    )


@pytest.fixture
def sample_index(sample_repo) -> GlobalIndex:
    return GlobalIndex(
        repos={"maestro-bff-api": sample_repo},
        last_updated="2026-01-01T00:00:00",
    )


VALID_ANALYSIS_DICT = {
    "title": "Listar candidatos por estado",
    "summary": "El dev necesita un endpoint para filtrar candidatos por su estado actual.",
    "repos_impacted": [
        {"repo": "maestro-bff-api", "project": "Cantera", "reason": "Nuevo endpoint GET", "touch_type": "MODIFICAR"}
    ],
    "endpoints_affected": ["GET /api/v1/candidates"],
    "schemas_affected": ["CandidateSchema"],
    "db_changes": [],
    "external_integrations": [],
    "complexity_signals": ["Paginación necesaria"],
    "open_questions": ["¿El filtro es configurable?"],
    "flow": [{"label": "GET /candidates", "detail": "router FastAPI", "kind": "endpoint"}],
    "markdown": "# Listar candidatos\n\n## Objetivo\nFiltrar candidatos por estado.",
}
