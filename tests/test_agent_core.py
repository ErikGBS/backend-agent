import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.core import _build_initial_content, _extract_analysis, run_agent
from src.models.query import AgentQuery, RefinementAnalysis

from tests.conftest import VALID_ANALYSIS_DICT


# --- helpers ---

def text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def tool_block(name: str, inputs: dict, block_id: str = "tb-1"):
    return SimpleNamespace(type="tool_use", name=name, input=inputs, id=block_id)


def api_response(stop_reason: str, content: list):
    return SimpleNamespace(stop_reason=stop_reason, content=content)


# --- _extract_analysis ---

class TestExtractAnalysis:
    def test_valid_json_returns_model(self):
        raw = json.dumps(VALID_ANALYSIS_DICT)
        result = _extract_analysis(raw)
        assert isinstance(result, RefinementAnalysis)
        assert result.title == "Listar candidatos por estado"

    def test_json_inside_code_fence(self):
        raw = f"```json\n{json.dumps(VALID_ANALYSIS_DICT)}\n```"
        result = _extract_analysis(raw)
        assert result is not None
        assert result.summary == VALID_ANALYSIS_DICT["summary"]

    def test_plain_code_fence(self):
        raw = f"```\n{json.dumps(VALID_ANALYSIS_DICT)}\n```"
        result = _extract_analysis(raw)
        assert result is not None

    def test_invalid_json_returns_none(self):
        assert _extract_analysis("not json at all") is None

    def test_missing_required_fields_returns_none(self):
        assert _extract_analysis('{"title": "only title"}') is None

    def test_empty_string_returns_none(self):
        assert _extract_analysis("") is None


# --- _build_initial_content ---

class TestBuildInitialContent:
    def test_query_only_produces_one_text_block(self):
        query = AgentQuery(query="Agregar endpoint de candidatos")
        content = _build_initial_content(query)
        assert len(content) == 1
        assert content[0]["type"] == "text"
        assert "Agregar endpoint de candidatos" in content[0]["text"]

    def test_with_image_prepends_image_block(self):
        query = AgentQuery(query="Test", image_base64="base64data", image_media_type="image/png")
        content = _build_initial_content(query)
        assert len(content) == 2
        assert content[0]["type"] == "image"
        assert content[0]["source"]["data"] == "base64data"
        assert content[0]["source"]["media_type"] == "image/png"
        assert content[1]["type"] == "text"

    def test_none_media_type_falls_back_to_jpeg(self):
        # The code does `query.image_media_type or "image/jpeg"`, so None → "image/jpeg"
        query = AgentQuery(query="Test", image_base64="abc", image_media_type=None)
        content = _build_initial_content(query)
        assert content[0]["source"]["media_type"] == "image/jpeg"


# --- run_agent ---

class TestRunAgent:
    async def test_end_turn_with_valid_json(self, sample_index):
        query = AgentQuery(query="Listar candidatos", project="Cantera")
        resp = api_response("end_turn", [text_block(json.dumps(VALID_ANALYSIS_DICT))])

        with patch("src.agent.core._client") as mock_client:
            mock_client.messages.create = AsyncMock(return_value=resp)
            result = await run_agent(query, sample_index)

        assert result.answer == VALID_ANALYSIS_DICT["markdown"]
        assert result.analysis is not None
        assert result.analysis.title == VALID_ANALYSIS_DICT["title"]
        assert result.repos_consulted == []
        assert result.files_fetched == []

    async def test_end_turn_invalid_json_returns_raw_text(self, sample_index):
        query = AgentQuery(query="Test")
        raw = "Respuesta libre sin JSON"
        resp = api_response("end_turn", [text_block(raw)])

        with patch("src.agent.core._client") as mock_client:
            mock_client.messages.create = AsyncMock(return_value=resp)
            result = await run_agent(query, sample_index)

        assert result.answer == raw
        assert result.analysis is None

    async def test_tool_use_then_end_turn(self, sample_index):
        query = AgentQuery(query="Test", project="Cantera")
        tb = tool_block("list_endpoints", {"repo": "maestro-bff-api"})
        tool_resp = api_response("tool_use", [tb])
        final_resp = api_response("end_turn", [text_block(json.dumps(VALID_ANALYSIS_DICT))])

        with patch("src.agent.core._client") as mock_client, \
             patch("src.agent.core.execute_tool", new=AsyncMock(return_value="[GET] /api/v1/candidates")) as mock_exec:
            mock_client.messages.create = AsyncMock(side_effect=[tool_resp, final_resp])
            result = await run_agent(query, sample_index)

        mock_exec.assert_called_once_with("list_endpoints", {"repo": "maestro-bff-api"}, sample_index)
        assert "maestro-bff-api" in result.repos_consulted
        assert result.analysis is not None

    async def test_fetch_file_tracked(self, sample_index):
        query = AgentQuery(query="Test")
        tb = tool_block(
            "fetch_file",
            {"repo": "maestro-bff-api", "path": "/app/services/candidate_service.py", "project": "Cantera"},
        )
        tool_resp = api_response("tool_use", [tb])
        final_resp = api_response("end_turn", [text_block(json.dumps(VALID_ANALYSIS_DICT))])

        with patch("src.agent.core._client") as mock_client, \
             patch("src.agent.core.execute_tool", new=AsyncMock(return_value="file content")):
            mock_client.messages.create = AsyncMock(side_effect=[tool_resp, final_resp])
            result = await run_agent(query, sample_index)

        assert "maestro-bff-api:/app/services/candidate_service.py" in result.files_fetched

    async def test_unexpected_stop_reason_returns_raw(self, sample_index):
        query = AgentQuery(query="Test")
        resp = api_response("max_tokens", [text_block("Respuesta truncada")])

        with patch("src.agent.core._client") as mock_client:
            mock_client.messages.create = AsyncMock(return_value=resp)
            result = await run_agent(query, sample_index)

        assert result.answer == "Respuesta truncada"

    async def test_max_tool_rounds_triggers_force_call(self, sample_index):
        from src.agent.core import _MAX_TOOL_ROUNDS

        query = AgentQuery(query="Test")
        tb = tool_block("list_endpoints", {"repo": "maestro-bff-api"})
        tool_responses = [api_response("tool_use", [tb])] * _MAX_TOOL_ROUNDS
        forced_response = api_response("end_turn", [text_block(json.dumps(VALID_ANALYSIS_DICT))])

        with patch("src.agent.core._client") as mock_client, \
             patch("src.agent.core.execute_tool", new=AsyncMock(return_value="result")):
            mock_client.messages.create = AsyncMock(side_effect=tool_responses + [forced_response])
            result = await run_agent(query, sample_index)

        # _MAX_TOOL_ROUNDS calls for tool_use + 1 forced final call
        assert mock_client.messages.create.call_count == _MAX_TOOL_ROUNDS + 1
        assert result.analysis is not None

    async def test_multiple_tools_in_one_round(self, sample_index):
        query = AgentQuery(query="Test")
        tb1 = tool_block("list_endpoints", {"repo": "maestro-bff-api"}, block_id="tb-1")
        tb2 = tool_block("search_code", {"query": "candidate service"}, block_id="tb-2")
        tool_resp = api_response("tool_use", [tb1, tb2])
        final_resp = api_response("end_turn", [text_block(json.dumps(VALID_ANALYSIS_DICT))])

        with patch("src.agent.core._client") as mock_client, \
             patch("src.agent.core.execute_tool", new=AsyncMock(return_value="result")) as mock_exec:
            mock_client.messages.create = AsyncMock(side_effect=[tool_resp, final_resp])
            await run_agent(query, sample_index)

        assert mock_exec.call_count == 2
