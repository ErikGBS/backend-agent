import base64
from unittest.mock import AsyncMock, call, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.models.index import EndpointInfo, GlobalIndex, RepoIndex

from tests.conftest import VALID_ANALYSIS_DICT


def basic_auth_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_index():
    repo = RepoIndex(
        name="maestro-bff-api", repo_id="repo-123", project="Cantera",
        repo_type="python", tree=[], endpoints=[], services=[], schemas=[], key_files={},
    )
    return GlobalIndex(repos={"maestro-bff-api": repo}, last_updated="2026-01-01T00:00:00")


def push_payload(repo_name: str = "maestro-bff-api", repo_id: str = "repo-123", project: str = "Cantera") -> dict:
    return {
        "resource": {
            "repository": {
                "id": repo_id,
                "name": repo_name,
                "project": {"name": project},
            }
        }
    }


# --- endpoint behavior ---

class TestWebhookEndpoint:
    def test_returns_accepted_immediately(self, client):
        response = client.post("/api/v1/webhook/azure-devops", json=push_payload())
        assert response.status_code == 200
        assert response.json() == {"status": "accepted"}

    def test_missing_repo_fields_still_returns_accepted(self, client):
        response = client.post("/api/v1/webhook/azure-devops", json={})
        assert response.status_code == 200
        assert response.json() == {"status": "accepted"}

    def test_no_secret_configured_accepts_without_auth(self, client):
        with patch("src.api.v1.webhook.settings") as mock_settings:
            mock_settings.webhook_secret = None
            response = client.post("/api/v1/webhook/azure-devops", json=push_payload())
        assert response.status_code == 200

    def test_valid_basic_auth_accepted(self, client):
        with patch("src.api.v1.webhook.settings") as mock_settings:
            mock_settings.webhook_secret = "azure:mysecret"
            response = client.post(
                "/api/v1/webhook/azure-devops",
                json=push_payload(),
                headers={"Authorization": basic_auth_header("azure", "mysecret")},
            )
        assert response.status_code == 200

    def test_wrong_password_returns_401(self, client):
        with patch("src.api.v1.webhook.settings") as mock_settings:
            mock_settings.webhook_secret = "azure:mysecret"
            response = client.post(
                "/api/v1/webhook/azure-devops",
                json=push_payload(),
                headers={"Authorization": basic_auth_header("azure", "wrongpassword")},
            )
        assert response.status_code == 401

    def test_missing_auth_header_returns_401(self, client):
        with patch("src.api.v1.webhook.settings") as mock_settings:
            mock_settings.webhook_secret = "azure:mysecret"
            response = client.post("/api/v1/webhook/azure-devops", json=push_payload())
        assert response.status_code == 401

    def test_malformed_auth_header_returns_401(self, client):
        with patch("src.api.v1.webhook.settings") as mock_settings:
            mock_settings.webhook_secret = "azure:mysecret"
            response = client.post(
                "/api/v1/webhook/azure-devops",
                json=push_payload(),
                headers={"Authorization": "Bearer sometoken"},
            )
        assert response.status_code == 401

    def test_partial_payload_does_not_trigger_reindex(self, client):
        # Missing project.name → repo_id + repo_name present but project empty → no task added
        payload = {"resource": {"repository": {"id": "r1", "name": "repo"}}}
        with patch("src.api.v1.webhook._reindex_repo") as mock_reindex:
            response = client.post("/api/v1/webhook/azure-devops", json=payload)
        assert response.status_code == 200
        mock_reindex.assert_not_called()


# --- _reindex_repo sync logic ---

class TestReindexRepo:
    async def test_syncs_both_json_and_qdrant(self, mock_index):
        from src.api.v1.webhook import _reindex_repo

        updated_repo = RepoIndex(
            name="maestro-bff-api", repo_id="repo-123", project="Cantera",
            repo_type="python", tree=["/app/test.py"],
            endpoints=[EndpointInfo(path="/api/v1/test", method="GET", file="app/test.py")],
            services=[], schemas=[], key_files={},
        )

        with patch("src.api.v1.webhook.load_index", return_value=mock_index), \
             patch("src.api.v1.webhook.AzureDevOpsClient") as mock_ado, \
             patch("src.api.v1.webhook.crawl_python_repo", new=AsyncMock(return_value=updated_repo)), \
             patch("src.api.v1.webhook._persist") as mock_persist, \
             patch("src.api.v1.webhook.ensure_collection") as mock_ensure, \
             patch("src.api.v1.webhook.delete_repo_chunks") as mock_delete, \
             patch("src.api.v1.webhook.index_repo_vectors") as mock_vectors:

            mock_ado.return_value.get_full_tree = AsyncMock(return_value=[
                {"path": "/app/test.py", "gitObjectType": "blob"}
            ])

            await _reindex_repo("Cantera", "maestro-bff-api", "repo-123")

        mock_persist.assert_called_once()
        mock_ensure.assert_called_once()
        mock_delete.assert_called_once_with("maestro-bff-api")
        mock_vectors.assert_called_once_with(updated_repo)

    async def test_delete_before_upsert_ordering(self, mock_index):
        """delete_repo_chunks must be called before index_repo_vectors."""
        from src.api.v1.webhook import _reindex_repo

        call_order = []
        dummy_repo = RepoIndex(
            name="maestro-bff-api", repo_id="repo-123", project="Cantera",
            repo_type="python", tree=[], endpoints=[], services=[], schemas=[], key_files={},
        )

        with patch("src.api.v1.webhook.load_index", return_value=mock_index), \
             patch("src.api.v1.webhook.AzureDevOpsClient") as mock_ado, \
             patch("src.api.v1.webhook.crawl_python_repo", new=AsyncMock(return_value=dummy_repo)), \
             patch("src.api.v1.webhook._persist"), \
             patch("src.api.v1.webhook.ensure_collection"), \
             patch("src.api.v1.webhook.delete_repo_chunks", side_effect=lambda _: call_order.append("delete")), \
             patch("src.api.v1.webhook.index_repo_vectors", side_effect=lambda _: call_order.append("upsert")):

            mock_ado.return_value.get_full_tree = AsyncMock(return_value=[])
            await _reindex_repo("Cantera", "maestro-bff-api", "repo-123")

        assert call_order == ["delete", "upsert"]

    async def test_no_index_file_skips_silently(self):
        from src.api.v1.webhook import _reindex_repo

        with patch("src.api.v1.webhook.load_index", return_value=None), \
             patch("src.api.v1.webhook._persist") as mock_persist:
            await _reindex_repo("Cantera", "maestro-bff-api", "repo-123")

        mock_persist.assert_not_called()

    async def test_exception_during_crawl_does_not_propagate(self, mock_index):
        from src.api.v1.webhook import _reindex_repo

        with patch("src.api.v1.webhook.load_index", return_value=mock_index), \
             patch("src.api.v1.webhook.AzureDevOpsClient") as mock_ado, \
             patch("src.api.v1.webhook.crawl_python_repo", new=AsyncMock(side_effect=Exception("Azure timeout"))), \
             patch("src.api.v1.webhook._persist") as mock_persist:

            mock_ado.return_value.get_full_tree = AsyncMock(return_value=[])
            await _reindex_repo("Cantera", "maestro-bff-api", "repo-123")

        mock_persist.assert_not_called()

    async def test_dotnet_repo_detected_by_csproj(self, mock_index):
        from src.api.v1.webhook import _reindex_repo

        dotnet_repo = RepoIndex(
            name="func-repo", repo_id="r2", project="Progresol",
            repo_type="dotnet", tree=[], endpoints=[], services=[], schemas=[], key_files={},
        )

        with patch("src.api.v1.webhook.load_index", return_value=mock_index), \
             patch("src.api.v1.webhook.AzureDevOpsClient") as mock_ado, \
             patch("src.api.v1.webhook.crawl_dotnet_repo", new=AsyncMock(return_value=dotnet_repo)) as mock_dotnet, \
             patch("src.api.v1.webhook.crawl_python_repo", new=AsyncMock()) as mock_python, \
             patch("src.api.v1.webhook._persist"), \
             patch("src.api.v1.webhook.ensure_collection"), \
             patch("src.api.v1.webhook.delete_repo_chunks"), \
             patch("src.api.v1.webhook.index_repo_vectors"):

            mock_ado.return_value.get_full_tree = AsyncMock(return_value=[
                {"path": "/MyFunc/MyFunc.csproj", "gitObjectType": "blob"},
            ])
            await _reindex_repo("Progresol", "func-repo", "r2")

        mock_dotnet.assert_called_once()
        mock_python.assert_not_called()
