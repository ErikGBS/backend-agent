from typing import TypedDict

from src.models.index import GlobalIndex
from src.models.query import AgentQuery, RefinementAnalysis
from src.agent.reflection import ReflectionResult


class AgentState(TypedDict):
    # Conversation history in raw Anthropic format
    messages: list[dict]

    # Input
    query: AgentQuery
    index: GlobalIndex

    # Tracking
    repos_used: set
    files_used: list
    tool_round: int
    reflection_round: int

    # Output
    analysis: RefinementAnalysis | None
    reflection: ReflectionResult | None

    # Human-in-the-loop
    # "approve" → accept analysis as-is
    # "investigate:<text>" → investigate additional context before finalizing
    # None → HITL not triggered yet
    human_decision: str | None
