from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.models.index import EndpointInfo, GlobalIndex, RepoIndex
from src.models.query import AgentResponse, RefinementAnalysis

from tests.conftest import VALID_ANALYSIS_DICT

API_KEY = "test-api-key"  # matches conftest os.environ["API_KEY"]


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_index():
    repo = RepoIndex(
        name="maestro-bff-api", repo_id="r1", project="Cantera",
        repo_type="python", tree=[], endpoints=[], services=[], schemas=[], key_files={},
    )
    return GlobalIndex(repos={"maestro-bff-api": repo}, last_updated="2026-01-01T00:00:00")


def make_agent_response() -> AgentResponse:
    analysis = RefinementAnalysis(**VALID_ANALYSIS_DICT)
    return AgentResponse(
        answer=analysis.markdown,
        analysis=analysis,
        repos_consulted=["maestro-bff-api"],
        files_fetched=["maestro-bff-api:/app/api/v1/candidates.py"],
    )


# --- /health ---

class TestHealth:
    def test_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


# --- POST /api/v1/agent/query ---

class TestAgentQuery:
    def test_missing_api_key_returns_401(self, client):
        response = client.post("/api/v1/agent/query", json={"query": "test"})
        assert response.status_code == 401

    def test_wrong_api_key_returns_403(self, client):
        response = client.post(
            "/api/v1/agent/query",
            json={"query": "test"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert response.status_code == 403

    def test_no_index_returns_503(self, client):
        with patch("src.api.v1.agent.load_index", return_value=None):
            response = client.post(
                "/api/v1/agent/query",
                json={"query": "test"},
                headers={"X-API-Key": API_KEY},
            )
        assert response.status_code == 503

    def test_successful_query_returns_agent_response(self, client, mock_index):
        agent_resp = make_agent_response()
        with patch("src.api.v1.agent.load_index", return_value=mock_index), \
             patch("src.api.v1.agent.run_agent", new=AsyncMock(return_value=agent_resp)):
            response = client.post(
                "/api/v1/agent/query",
                json={"query": "Agregar endpoint de candidatos", "project": "Cantera"},
                headers={"X-API-Key": API_KEY},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["answer"] == agent_resp.answer
        assert data["repos_consulted"] == ["maestro-bff-api"]
        assert len(data["files_fetched"]) == 1

    def test_query_passes_body_to_run_agent(self, client, mock_index):
        agent_resp = make_agent_response()
        with patch("src.api.v1.agent.load_index", return_value=mock_index), \
             patch("src.api.v1.agent.run_agent", new=AsyncMock(return_value=agent_resp)) as mock_run:
            client.post(
                "/api/v1/agent/query",
                json={"query": "Lista de candidatos", "project": "Cantera"},
                headers={"X-API-Key": API_KEY},
            )
        call_args = mock_run.call_args
        query_arg = call_args[0][0]
        assert query_arg.query == "Lista de candidatos"
        assert query_arg.project == "Cantera"

    def test_analysis_null_when_agent_returns_no_json(self, client, mock_index):
        agent_resp = AgentResponse(
            answer="Texto libre sin JSON",
            analysis=None,
            repos_consulted=[],
            files_fetched=[],
        )
        with patch("src.api.v1.agent.load_index", return_value=mock_index), \
             patch("src.api.v1.agent.run_agent", new=AsyncMock(return_value=agent_resp)):
            response = client.post(
                "/api/v1/agent/query",
                json={"query": "test"},
                headers={"X-API-Key": API_KEY},
            )
        assert response.status_code == 200
        assert response.json()["analysis"] is None


# --- POST /api/v1/agent/report ---

class TestAgentReport:
    def test_missing_api_key_returns_401(self, client):
        response = client.post("/api/v1/agent/report", json={"query": "test"})
        assert response.status_code == 401

    def test_no_index_returns_503(self, client):
        with patch("src.api.v1.agent.load_index", return_value=None):
            response = client.post(
                "/api/v1/agent/report",
                json={"query": "test"},
                headers={"X-API-Key": API_KEY},
            )
        assert response.status_code == 503

    def test_returns_html_response(self, client, mock_index):
        agent_resp = make_agent_response()
        with patch("src.api.v1.agent.load_index", return_value=mock_index), \
             patch("src.api.v1.agent.run_agent", new=AsyncMock(return_value=agent_resp)):
            response = client.post(
                "/api/v1/agent/report",
                json={"query": "test"},
                headers={"X-API-Key": API_KEY},
            )
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_html_contains_repos_consulted(self, client, mock_index):
        agent_resp = make_agent_response()
        with patch("src.api.v1.agent.load_index", return_value=mock_index), \
             patch("src.api.v1.agent.run_agent", new=AsyncMock(return_value=agent_resp)):
            response = client.post(
                "/api/v1/agent/report",
                json={"query": "test"},
                headers={"X-API-Key": API_KEY},
            )
        assert "maestro-bff-api" in response.text

    def test_html_contains_rendered_markdown(self, client, mock_index):
        agent_resp = make_agent_response()
        with patch("src.api.v1.agent.load_index", return_value=mock_index), \
             patch("src.api.v1.agent.run_agent", new=AsyncMock(return_value=agent_resp)):
            response = client.post(
                "/api/v1/agent/report",
                json={"query": "test"},
                headers={"X-API-Key": API_KEY},
            )
        # The markdown "# Listar candidatos\n..." should render to an h1 tag
        assert "<h1" in response.text
