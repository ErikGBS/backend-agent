from typing import TypedDict

from src.models.index import GlobalIndex
from src.models.query import AgentQuery, RefinementAnalysis
from src.agent.reflection import ReflectionResult


class AgentState(TypedDict):
    # Conversation history — all content serialized to plain dicts (JSON-safe)
    messages: list[dict]

    # Input
    query: AgentQuery
    index: GlobalIndex

    # Tracking — list instead of set for JSON serialization (MemorySaver checkpointing)
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
