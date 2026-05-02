from typing import TypedDict

from src.models.query import AgentQuery, RefinementAnalysis
from src.agent.reflection import ReflectionResult


class AgentState(TypedDict):
    # Conversation history — all content serialized to plain dicts (JSON-safe)
    messages: list[dict]

    # Input — AgentQuery is a Pydantic model, JSON-serializable for all checkpointers
    # GlobalIndex is intentionally excluded: it's a complex object not suited for
    # serialization. Pass it via partial() to nodes that need it (node_execute_tools).
    query: AgentQuery

    # Tracking — list (not set) for JSON serialization compatibility
    repos_used: list[str]
    files_used: list[str]
    tool_round: int
    reflection_round: int

    # Serializable signal from node_call_model (replaces raw Anthropic response object)
    _stop_reason: str

    # Output
    analysis: RefinementAnalysis | None
    reflection: ReflectionResult | None

    # Human-in-the-loop
    # "approve" → accept analysis as-is
    # "investigate:<text>" → investigate additional context before finalizing
    # None → HITL not triggered yet
    human_decision: str | None
