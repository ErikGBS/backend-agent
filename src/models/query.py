from pydantic import BaseModel


class AgentQuery(BaseModel):
    query: str
    project: str | None = None  # "Cantera" | "Progresol" | None (ambos)
    image_base64: str | None = None
    image_media_type: str | None = "image/jpeg"


class PlanStep(BaseModel):
    number: int
    title: str
    mode: str           # "NUEVO" | "MODIFICAR"
    path: str
    language: str       # "python" | "csharp" | "sql" | ...
    code: str
    description: str | None = None


class PlanFlowNode(BaseModel):
    label: str
    detail: str | None = None
    kind: str | None = None  # endpoint | schema | service | repo | orm | mapper | external


class PlanChecklistGroup(BaseModel):
    category: str
    items: list[str]


class AgentPlan(BaseModel):
    title: str
    steps: list[PlanStep]
    flow: list[PlanFlowNode]
    checklist: list[PlanChecklistGroup]
    markdown: str


class AgentResponse(BaseModel):
    answer: str                       # markdown completo (compat con clientes viejos)
    plan: AgentPlan | None = None     # None si el agente no devolvió JSON válido
    repos_consulted: list[str]
    files_fetched: list[str]
