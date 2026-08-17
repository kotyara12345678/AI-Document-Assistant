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
    context_document_ids: list[int] | None = Field(
        default=None,
        description=(
            "Documents the user explicitly attached as context for this turn. "
            "Takes precedence over document_ids/document_id and over RAG retrieval; "
            "the agent should use these documents directly (e.g. for edits)."
        ),
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


class AgentStep(BaseModel):
    """One observable action the agent performed (safe action log only)."""

    step_id: str
    tool: str
    message: str
    status: str  # running | completed | error


class AgentEvent(BaseModel):
    """A single realtime event emitted while the agent runs.

    Allowed ``type`` values: ``agent_step`` (status running/completed/error),
    ``document_created``, ``final``. No chain-of-thought or internal prompts are
    ever sent through this channel.
    """

    type: str
    step_id: str | None = None
    status: str | None = None
    tool: str | None = None
    message: str | None = None
    content: str | None = None
    document_id: int | None = None
    filename: str | None = None
    download_url: str | None = None


class AgentResponse(BaseModel):
    answer: str
    tool_calls: list[AgentToolCall] = Field(default_factory=list)
    tool_results: list[AgentToolResult] = Field(default_factory=list)
    agent_steps: list[AgentStep] = Field(default_factory=list)
    chat_id: int = 0
    sources: list[dict] = Field(default_factory=list)
    created_documents: list[dict] = Field(default_factory=list)
