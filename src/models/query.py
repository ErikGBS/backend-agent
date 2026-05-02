from pydantic import BaseModel


class AgentQuery(BaseModel):
    query: str
    project: str | None = None  # "Cantera" | "Progresol" | None (ambos)
    image_base64: str | None = None
    image_media_type: str | None = "image/jpeg"


class RepoImpact(BaseModel):
    repo: str
    project: str                 # "Cantera" | "Progresol"
    reason: str
    touch_type: str              # "MODIFICAR" | "NUEVO" | "INVESTIGAR"


class FlowNode(BaseModel):
    label: str
    detail: str | None = None
    kind: str | None = None      # endpoint | schema | service | repo | orm | mapper | external


class RefinementAnalysis(BaseModel):
    title: str
    summary: str
    repos_impacted: list[RepoImpact]
    endpoints_affected: list[str]
    schemas_affected: list[str]
    db_changes: list[str]
    external_integrations: list[str]
    complexity_signals: list[str]
    open_questions: list[str]
    flow: list[FlowNode]
    markdown: str


class AgentResponse(BaseModel):
    answer: str
    analysis: RefinementAnalysis | None = None
    repos_consulted: list[str]
    files_fetched: list[str]
    reflection_approved: bool | None = None
    reflection_verdict: str | None = None
