import markdown as md
from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import APIKeyHeader

from src.agent.core import run_agent, run_agent_stream, run_graph
from src.core.config import settings
from src.indexer.index_builder import load_index
from src.models.query import AgentQuery, AgentResponse

router = APIRouter(prefix="/agent", tags=["agent"])

_api_key_header = APIKeyHeader(name="X-API-Key")

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Backend Agent — Respuesta</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            max-width: 900px; margin: 40px auto; padding: 0 24px;
            background: #f9fafb; color: #111827; }}
    h1,h2,h3 {{ color: #1e3a5f; }}
    pre {{ background: #1e1e1e; color: #d4d4d4; padding: 16px; border-radius: 8px;
           overflow-x: auto; font-size: 13px; }}
    code {{ background: #e5e7eb; padding: 2px 6px; border-radius: 4px; font-size: 13px; }}
    pre code {{ background: none; padding: 0; }}
    blockquote {{ border-left: 4px solid #3b82f6; margin: 0; padding: 8px 16px;
                  background: #eff6ff; color: #1d4ed8; border-radius: 0 4px 4px 0; }}
    .meta {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;
             padding: 12px 16px; margin-bottom: 24px; font-size: 13px; color: #6b7280; }}
    .meta span {{ font-weight: 600; color: #374151; }}
    hr {{ border: none; border-top: 1px solid #e5e7eb; margin: 24px 0; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 8px 12px; text-align: left; }}
    th {{ background: #f3f4f6; }}
  </style>
</head>
<body>
  <div class="meta">
    <b>Repos consultados:</b> <span>{repos}</span> &nbsp;|&nbsp;
    <b>Archivos leídos:</b> <span>{files_count}</span>
  </div>
  {content}
</body>
</html>"""


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


@router.post("/query/v2", response_model=AgentResponse)
async def query_agent_v2(
    body: AgentQuery,
    _: str = Depends(_verify_api_key),
) -> AgentResponse:
    """LangGraph-powered endpoint. Same contract as /query but backed by the state graph."""
    index = load_index()
    if not index:
        raise HTTPException(status_code=503, detail="Índice no disponible. Ejecuta el indexador primero.")
    from src.agent.core import _raw_client
    return await run_graph(body, index, _raw_client)


@router.post("/stream")
async def stream_agent(
    body: AgentQuery,
    _: str = Depends(_verify_api_key),
) -> StreamingResponse:
    """
    Streaming endpoint via Server-Sent Events (SSE).
    Emite eventos en tiempo real mientras el agente trabaja:
      - {"event": "start"}           — inicio del análisis
      - {"event": "progress", "tool": "...", "label": "...", "detail": "..."}  — tool en ejecución
      - {"event": "token", "text": "..."}   — fragmentos del análisis final
      - {"event": "done", "repos": [...], "files": [...]}  — análisis completo
    """
    index = load_index()
    if not index:
        raise HTTPException(status_code=503, detail="Índice no disponible. Ejecuta el indexador primero.")
    return StreamingResponse(
        run_agent_stream(body, index),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/report", response_class=HTMLResponse)
async def query_agent_report(
    body: AgentQuery,
    _: str = Depends(_verify_api_key),
) -> HTMLResponse:
    index = load_index()
    if not index:
        raise HTTPException(status_code=503, detail="Índice no disponible. Ejecuta el indexador primero.")

    result = await run_agent(body, index)
    html_content = md.markdown(
        result.answer,
        extensions=["fenced_code", "tables", "nl2br", "toc"],
    )
    page = _HTML_TEMPLATE.format(
        repos=", ".join(result.repos_consulted) or "—",
        files_count=len(result.files_fetched),
        content=html_content,
    )
    return HTMLResponse(content=page)
