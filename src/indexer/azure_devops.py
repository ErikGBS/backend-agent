import base64

import httpx

from src.core.config import settings


class AzureDevOpsClient:
    def __init__(self) -> None:
        token = base64.b64encode(f":{settings.azure_devops_pat}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }
        self._base = f"https://dev.azure.com/{settings.azure_devops_org}"

    async def list_repos(self, project: str) -> list[dict]:
        url = f"{self._base}/{project}/_apis/git/repositories?api-version=7.1"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=self._headers)
            resp.raise_for_status()
            return resp.json()["value"]

    async def get_full_tree(self, project: str, repo_id: str) -> list[dict]:
        url = (
            f"{self._base}/{project}/_apis/git/repositories/{repo_id}/items"
            f"?recursionLevel=Full&api-version=7.1"
        )
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url, headers=self._headers)
            resp.raise_for_status()
            return resp.json().get("value", [])

    async def get_file_content(self, project: str, repo_id: str, path: str) -> str:
        url = (
            f"{self._base}/{project}/_apis/git/repositories/{repo_id}/items"
            f"?path={path}&api-version=7.1"
        )
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=self._headers)
            resp.raise_for_status()
            return resp.text
