from pydantic import BaseModel, Field


class AgentRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    document_id: int | None = Field(
        default=None,
        description="Limit the agent's document search to a single document.",
    )
    document_ids: list[int] | None = Field(
        default=None,
        description="Limit the agent's document search to several documents. Takes precedence over document_id.",
    )
    chat_id: int | None = Field(
        default=None,
        description="Chat to attach this turn to; resolved/created by the backend if omitted.",
    )


class AgentToolCall(BaseModel):
    """A single function call the model requested (for observability)."""

    name: str
    arguments: dict = Field(default_factory=dict)


class AgentToolResult(BaseModel):
    """The backend's result for one tool call, echoed back to the caller."""

    tool_call_id: str
    name: str
    content: str


class AgentResponse(BaseModel):
    answer: str
    tool_calls: list[AgentToolCall] = Field(default_factory=list)
    tool_results: list[AgentToolResult] = Field(default_factory=list)
    chat_id: int = 0
    sources: list[dict] = Field(default_factory=list)
    created_documents: list[dict] = Field(default_factory=list)
