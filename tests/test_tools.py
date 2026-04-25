from unittest.mock import AsyncMock, patch

import pytest

from src.agent.tools import execute_tool
from src.models.index import EndpointInfo, GlobalIndex, RepoIndex


_SENTINEL = object()


def make_index(
    endpoints=_SENTINEL,
    services=_SENTINEL,
    schemas=_SENTINEL,
    repo_name="maestro-bff-api",
    project="Cantera",
) -> GlobalIndex:
    repo = RepoIndex(
        name=repo_name,
        repo_id="repo-123",
        project=project,
        repo_type="python",
        tree=[],
        endpoints=endpoints if endpoints is not _SENTINEL else [
            EndpointInfo(path="/api/v1/candidates", method="GET", file="app/api/v1/candidates.py"),
        ],
        services=services if services is not _SENTINEL else ["CandidateService"],
        schemas=schemas if schemas is not _SENTINEL else ["CandidateSchema", "CandidateResponse"],
        key_files={},
    )
    return GlobalIndex(repos={repo_name: repo}, last_updated="2026-01-01T00:00:00")


# --- search_code ---

class TestSearchCode:
    async def test_returns_formatted_hits(self):
        index = make_index()
        hits = [{"repo": "maestro-bff-api", "file_path": "app/api/v1/candidates.py", "score": 0.92, "text": "class CandidateService: pass"}]
        with patch("src.agent.tools.vector_search", return_value=hits):
            result = await execute_tool("search_code", {"query": "candidate service"}, index)
        assert "maestro-bff-api" in result
        assert "0.92" in result
        assert "CandidateService" in result

    async def test_no_results_returns_message(self):
        index = make_index()
        with patch("src.agent.tools.vector_search", return_value=[]):
            result = await execute_tool("search_code", {"query": "xyz nothing"}, index)
        assert "No se encontraron" in result

    async def test_passes_project_filter(self):
        index = make_index()
        with patch("src.agent.tools.vector_search", return_value=[]) as mock_search:
            await execute_tool("search_code", {"query": "test", "project": "Cantera"}, index)
        mock_search.assert_called_once_with("test", top_k=6, project="Cantera")

    async def test_omits_project_filter_when_not_provided(self):
        index = make_index()
        with patch("src.agent.tools.vector_search", return_value=[]) as mock_search:
            await execute_tool("search_code", {"query": "test"}, index)
        mock_search.assert_called_once_with("test", top_k=6, project=None)


# --- fetch_file ---

class TestFetchFile:
    async def test_success_returns_file_content(self):
        index = make_index()
        with patch("src.agent.tools.AzureDevOpsClient") as mock_cls:
            mock_cls.return_value.get_file_content = AsyncMock(return_value="def get_candidates(): ...")
            result = await execute_tool(
                "fetch_file",
                {"repo": "maestro-bff-api", "path": "/app/api/v1/candidates.py", "project": "Cantera"},
                index,
            )
        assert "maestro-bff-api" in result
        assert "def get_candidates" in result

    async def test_uses_repo_id_from_index(self):
        index = make_index()
        with patch("src.agent.tools.AzureDevOpsClient") as mock_cls:
            mock_get = AsyncMock(return_value="content")
            mock_cls.return_value.get_file_content = mock_get
            await execute_tool(
                "fetch_file",
                {"repo": "maestro-bff-api", "path": "/app/test.py", "project": "Cantera"},
                index,
            )
        mock_get.assert_called_once_with("Cantera", "repo-123", "/app/test.py")

    async def test_error_returns_error_message(self):
        index = make_index()
        with patch("src.agent.tools.AzureDevOpsClient") as mock_cls:
            mock_cls.return_value.get_file_content = AsyncMock(side_effect=Exception("Connection refused"))
            result = await execute_tool(
                "fetch_file",
                {"repo": "maestro-bff-api", "path": "/app/test.py", "project": "Cantera"},
                index,
            )
        assert "Error" in result
        assert "Connection refused" in result

    async def test_unknown_repo_falls_back_to_repo_name_as_id(self):
        index = make_index()
        with patch("src.agent.tools.AzureDevOpsClient") as mock_cls:
            mock_get = AsyncMock(return_value="content")
            mock_cls.return_value.get_file_content = mock_get
            await execute_tool(
                "fetch_file",
                {"repo": "unknown-repo", "path": "/app/test.py", "project": "Cantera"},
                index,
            )
        # repo not in index → uses the repo name as repo_id
        mock_get.assert_called_once_with("Cantera", "unknown-repo", "/app/test.py")


# --- list_endpoints ---

class TestListEndpoints:
    async def test_returns_endpoints_formatted(self):
        index = make_index()
        result = await execute_tool("list_endpoints", {"repo": "maestro-bff-api"}, index)
        assert "[GET] /api/v1/candidates" in result
        assert "app/api/v1/candidates.py" in result

    async def test_multiple_endpoints(self):
        index = make_index(endpoints=[
            EndpointInfo(path="/api/v1/a", method="GET", file="a.py"),
            EndpointInfo(path="/api/v1/b", method="POST", file="b.py"),
        ])
        result = await execute_tool("list_endpoints", {"repo": "maestro-bff-api"}, index)
        assert "[GET]" in result
        assert "[POST]" in result

    async def test_repo_not_found_lists_available(self):
        index = make_index()
        result = await execute_tool("list_endpoints", {"repo": "nonexistent"}, index)
        assert "no encontrado" in result
        assert "maestro-bff-api" in result

    async def test_repo_with_no_endpoints(self):
        index = make_index(endpoints=[])
        result = await execute_tool("list_endpoints", {"repo": "maestro-bff-api"}, index)
        assert "No se encontraron endpoints" in result


# --- get_schema ---

class TestGetSchema:
    async def test_finds_schema_exact_match(self):
        index = make_index()
        result = await execute_tool("get_schema", {"class_name": "CandidateSchema"}, index)
        assert "CandidateSchema" in result

    async def test_case_insensitive_match(self):
        index = make_index()
        result = await execute_tool("get_schema", {"class_name": "candidateschema"}, index)
        assert "CandidateSchema" in result

    async def test_partial_match(self):
        index = make_index()
        # "Candidate" is a substring of "CandidateSchema" and "CandidateResponse"
        result = await execute_tool("get_schema", {"class_name": "Candidate"}, index)
        assert "CandidateSchema" in result or "CandidateResponse" in result

    async def test_filters_by_project(self):
        index = make_index(project="Cantera")
        with patch("src.agent.tools.vector_search", return_value=[]) as mock_search:
            await execute_tool("get_schema", {"class_name": "NonExistent", "project": "Progresol"}, index)
        # Should call vector_search as fallback with project filter
        mock_search.assert_called_once()
        _, kwargs = mock_search.call_args
        assert kwargs.get("project") == "Progresol"

    async def test_fallback_to_vector_search_when_not_in_index(self):
        index = make_index()
        hits = [{"repo": "maestro-bff-api", "file_path": "app/schemas/other.py", "text": "class UnknownClass: pass"}]
        with patch("src.agent.tools.vector_search", return_value=hits):
            result = await execute_tool("get_schema", {"class_name": "UnknownClass"}, index)
        assert "UnknownClass" in result or "other.py" in result

    async def test_not_found_anywhere(self):
        index = make_index()
        with patch("src.agent.tools.vector_search", return_value=[]):
            result = await execute_tool("get_schema", {"class_name": "GhostClass"}, index)
        assert "No se encontró" in result
        assert "GhostClass" in result


# --- unknown tool ---

class TestUnknownTool:
    async def test_returns_not_recognized_message(self):
        index = make_index()
        result = await execute_tool("nonexistent_tool", {}, index)
        assert "no reconocida" in result
        assert "nonexistent_tool" in result
