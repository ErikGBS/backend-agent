import logging
import sys

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

from src.api.v1.agent import router as agent_router
from src.api.v1.webhook import router as webhook_router

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

app = FastAPI(
    title="Backend Agent",
    description="Agente de IA para guiar implementaciones backend en Cantera y Progresol",
    version="0.1.0",
)

app.include_router(agent_router, prefix="/api/v1")
app.include_router(webhook_router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/ui", include_in_schema=False)
async def ui() -> FileResponse:
    return FileResponse("src/static/ui.html", media_type="text/html")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
