from pydantic import BaseModel


class AgentQuery(BaseModel):
    query: str
    project: str | None = None  # "Cantera" | "Progresol" | None (ambos)
    image_base64: str | None = None
    image_media_type: str | None = "image/jpeg"


class AgentResponse(BaseModel):
    answer: str
    repos_consulted: list[str]
    files_fetched: list[str]
