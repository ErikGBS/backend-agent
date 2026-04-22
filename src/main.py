from fastapi import FastAPI

from src.api.v1.agent import router as agent_router
from src.api.v1.webhook import router as webhook_router

app = FastAPI(
    title="Backend Agent",
    description="Agente de IA para guiar implementaciones backend en Cantera y Progresol",
    version="0.1.0",
)

app.include_router(agent_router, prefix="/api/v1")
app.include_router(webhook_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
