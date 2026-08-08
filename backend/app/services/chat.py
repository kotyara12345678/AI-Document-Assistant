from app.schemas.chat import ChatRequest, ChatResponse, SourceRef


def answer_question(request: ChatRequest) -> ChatResponse:
    """Stub implementation. RAG pipeline (retrieval + generation) comes later."""
    scope = f" in document #{request.document_id}" if request.document_id else ""
    return ChatResponse(
        answer=f"[stub] AI will answer: {request.question}{scope}",
        sources=[SourceRef(document_id=0, snippet="No sources yet — indexing not implemented.")],
    )
