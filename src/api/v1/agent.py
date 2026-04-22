from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

from src.agent.core import run_agent
from src.core.config import settings
from src.indexer.index_builder import load_index
from src.models.query import AgentQuery, AgentResponse

router = APIRouter(prefix="/agent", tags=["agent"])

_api_key_header = APIKeyHeader(name="X-API-Key")


def _verify_api_key(key: str = Security(_api_key_header)) -> str:
    if key != settings.api_key:
        raise HTTPException(status_code=403, detail="API key inválida")
    return key


@router.post("/query", response_model=AgentResponse)
async def query_agent(
    body: AgentQuery,
    _: str = Depends(_verify_api_key),
) -> AgentResponse:
    index = load_index()
    if not index:
        raise HTTPException(status_code=503, detail="Índice no disponible. Ejecuta el indexador primero.")
    return await run_agent(body, index)
