"""RAG chat: retrieve chunks from Qdrant, build context, ask Gemini.

Architecture: routes -> chat service -> retrieval service -> Gemini.
"""

import logging

from app.core.config import settings
from app.schemas.chat import ChatRequest, ChatResponse, SourceRef
from app.services import gemini
from app.services.retrieval import retrieve_context

logger = logging.getLogger("app.chat")

SYSTEM_INSTRUCTION = (
    "You are a helpful assistant that answers questions strictly based on the "
    "provided context extracted from user documents. "
    "Answer in the same language as the question. "
    "If the answer is not present in the context, say clearly that you could "
    "not find this information in the documents. Do not invent facts."
)


def _build_prompt(question: str, chunks) -> str:
    context_blocks = []
    for i, chunk in enumerate(chunks, start=1):
        context_blocks.append(
            f"[Document {chunk.source.document_id}, filename "
            f"'{chunk.source.filename}', chunk {chunk.source.chunk_index}, "
            f"score {chunk.score:.3f}]\n{chunk.text}"
        )
    context = "\n\n".join(context_blocks)
    return (
        "Use the following context from user documents to answer the question.\n\n"
        f"CONTEXT:\n{context}\n\n"
        f"QUESTION: {question}\n\n"
        "ANSWER:"
    )


def answer_question(request: ChatRequest, user_id: int) -> ChatResponse:
    chunks = retrieve_context(
        question=request.question,
        user_id=user_id,
        document_id=request.document_id,
        top_k=settings.CHAT_TOP_K,
    )

    if not chunks:
        return ChatResponse(
            answer="I could not find relevant information in the documents to answer this question.",
            sources=[],
        )

    prompt = _build_prompt(request.question, chunks)
    try:
        answer = gemini.generate_answer(prompt, system_instruction=SYSTEM_INSTRUCTION)
    except gemini.GeminiError:
        # Honest degradation: fall back to the top chunk instead of failing.
        logger.exception("Gemini failed; returning top chunk as answer")
        answer = f"[Gemini unavailable] Best match: {chunks[0].text[:500]}"

    sources = [chunk.source for chunk in chunks]
    return ChatResponse(answer=answer, sources=sources)
