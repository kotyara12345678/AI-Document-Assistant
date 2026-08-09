"""RAG chat: retrieve chunks from Qdrant, build context, ask Gemini.

Conversational context: every turn is persisted to PostgreSQL (chat_messages)
and scoped to a single chat (chats). On each request the last
CHAT_HISTORY_MESSAGES turns of that chat are sent to the LLM verbatim;
anything older is rolled into a compact summary stored in chat_summaries so
we never send the full history (token economy).

Architecture: routes -> chat service -> retrieval service -> Gemini.
"""

import logging

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.chat import Chat
from app.models.chat_message import ChatMessage, ChatSummary
from app.schemas.chat import ChatRequest, ChatResponse, SourceRef
from app.services import gemini
from app.services.retrieval import retrieve_context

logger = logging.getLogger("app.chat")

# Temporary routing: messages starting with this prefix go to plain GigaChat
# (no RAG retrieval); anything else uses the usual RAG pipeline.
DIRECT_CHAT_PREFIX = "@ai"

# Title given to fresh chats; replaced by the first question once asked.
DEFAULT_CHAT_TITLE = "Новый чат"

TITLE_MAX_LEN = 48


SYSTEM_INSTRUCTION = (
    "You are a helpful assistant that answers questions strictly based on the "
    "provided context extracted from user documents. "
    "Answer in the same language as the question. "
    "If the answer is not present in the context, say clearly that you could "
    "not find this information in the documents. Do not invent facts. "
    "You can use the conversation history to resolve follow-up questions "
    "(pronouns, 'it', 'this' etc.), but never answer from history alone: "
    "always ground the answer in the provided context."
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


def _make_title(question: str) -> str:
    """Short one-line title derived from the first question."""
    title = " ".join(question.strip().split())
    return title[:TITLE_MAX_LEN] or DEFAULT_CHAT_TITLE


# --- chat resolution --------------------------------------------------------


def resolve_chat(db: Session, user_id: int, chat_id: int | None) -> Chat:
    """Return the chat to run this turn in.

    A provided chat_id must belong to the user. When it is omitted, the most
    recently used chat is reused (so plain clients keep one conversation); if
    the user has no chats yet, a fresh one is created.
    """
    if chat_id is not None:
        chat = db.query(Chat).filter(Chat.id == chat_id, Chat.user_id == user_id).first()
        if chat is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Chat not found",
            )
        return chat

    chat = (
        db.query(Chat)
        .filter(Chat.user_id == user_id)
        .order_by(Chat.updated_at.desc())
        .first()
    )
    if chat is None:
        chat = Chat(user_id=user_id, title=DEFAULT_CHAT_TITLE)
        db.add(chat)
        db.commit()
        db.refresh(chat)
    return chat


# --- persistence ------------------------------------------------------------


def _save_message(
    db: Session, user_id: int, chat_id: int, role: str, content: str
) -> ChatMessage:
    message = ChatMessage(user_id=user_id, chat_id=chat_id, role=role, content=content)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def _recent_history(
    db: Session, chat_id: int, before_id: int | None = None
) -> list[dict[str, str]]:
    """Return the most recent CHAT_HISTORY_MESSAGES turns as LLM messages.

    The current question is excluded (it is already part of the RAG prompt),
    so the history holds only earlier turns, oldest first.
    """
    query = db.query(ChatMessage).filter(ChatMessage.chat_id == chat_id)
    if before_id is not None:
        query = query.filter(ChatMessage.id < before_id)
    rows = (
        query.order_by(ChatMessage.id.desc())
        .limit(settings.CHAT_HISTORY_MESSAGES)
        .all()
    )
    return [{"role": row.role, "content": row.content} for row in reversed(rows)]


def _make_summary(
    db: Session, chat: Chat, before_id: int | None = None
) -> str | None:
    """Generate (or refresh) a rolling summary of the older history.

    Returns None when the whole history still fits in the verbatim window, so
    nothing has to be summarized yet. Otherwise returns the summary text and
    persists it, summarising only turns not yet covered by a previous summary.
    """
    chat_id = chat.id
    history_limit = settings.CHAT_HISTORY_MESSAGES
    threshold = settings.CHAT_SUMMARY_THRESHOLD

    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.chat_id == chat_id)
        .order_by(ChatMessage.id.asc())
        .all()
    )
    if before_id is not None:
        rows = [row for row in rows if row.id < before_id]
    total = len(rows)

    if total <= history_limit + threshold:
        # History is still small: keep everything in the verbatim window.
        return None

    # Everything older than the verbatim window needs to be summarized.
    old_rows = rows[: total - history_limit] if history_limit else rows
    if not old_rows:
        return None

    summary_row = (
        db.query(ChatSummary)
        .filter(ChatSummary.chat_id == chat_id)
        .first()
    )

    base_summary = summary_row.summary if summary_row else ""
    new_turns = [
        row for row in old_rows
        if summary_row is None or row.id > summary_row.last_message_id
    ]

    if not new_turns:
        return base_summary or None

    conversation_text = "\n".join(
        f"{row.role.upper()}: {row.content}" for row in new_turns
    )
    if base_summary:
        prompt = (
            f"Previous summary:\n{base_summary}\n\n"
            f"New conversation turns to fold in:\n{conversation_text}\n\n"
            "Produce the updated summary."
        )
    else:
        prompt = conversation_text

    try:
        summary = gemini.generate_answer(
            prompt, system_instruction=settings.CHAT_SUMMARY_INSTRUCTION
        )
    except gemini.GeminiError:
        logger.exception("Summary generation failed; keeping previous summary")
        return base_summary or None

    if summary_row is None:
        summary_row = ChatSummary(
            user_id=chat.user_id, chat_id=chat_id, summary=summary
        )
        db.add(summary_row)
    else:
        summary_row.summary = summary
    summary_row.last_message_id = old_rows[-1].id if old_rows else 0
    db.commit()

    return summary


# --- main entry point -------------------------------------------------------


def answer_question(request: ChatRequest, user_id: int, db: Session) -> ChatResponse:
    """Answer a question, persisting the turn and using conversational context."""
    chat = resolve_chat(db, user_id, request.chat_id)

    # Persist the user's message first so it participates in the context.
    user_message = _save_message(db, user_id, chat.id, "user", request.question)

    # Name the chat after the first question.
    if chat.title == DEFAULT_CHAT_TITLE:
        chat.title = _make_title(request.question)
        db.commit()

    # Conversational context: recent turns verbatim + summary of older history.
    history = _recent_history(db, chat.id, before_id=user_message.id)
    summary = _make_summary(db, chat, before_id=user_message.id)

    # Temporary routing: "@ai ..." -> plain GigaChat without RAG.
    stripped = request.question.strip()
    if stripped.startswith(DIRECT_CHAT_PREFIX):
        direct_question = stripped[len(DIRECT_CHAT_PREFIX):].strip() or stripped
        try:
            answer = gemini.generate_answer(
                direct_question,
                system_instruction=SYSTEM_INSTRUCTION,
                history=history,
                summary=summary,
            )
        except gemini.GeminiError:
            logger.exception("Gemini failed in direct mode")
            answer = "[Gemini unavailable] Please try again later."
        _save_message(db, user_id, chat.id, "assistant", answer)
        return ChatResponse(chat_id=chat.id, answer=answer, sources=[])

    chunks = retrieve_context(
        question=request.question,
        user_id=user_id,
        document_id=request.document_id,
        top_k=settings.CHAT_TOP_K,
    )

    if not chunks:
        answer = (
            "I could not find relevant information in the documents to answer this question."
        )
        _save_message(db, user_id, chat.id, "assistant", answer)
        return ChatResponse(chat_id=chat.id, answer=answer, sources=[])

    prompt = _build_prompt(request.question, chunks)
    try:
        answer = gemini.generate_answer(
            prompt,
            system_instruction=SYSTEM_INSTRUCTION,
            history=history,
            summary=summary,
        )
    except gemini.GeminiError:
        # Honest degradation: fall back to the top chunk instead of failing.
        logger.exception("Gemini failed; returning top chunk as answer")
        answer = f"[Gemini unavailable] Best match: {chunks[0].text[:500]}"

    _save_message(db, user_id, chat.id, "assistant", answer)

    sources = [chunk.source for chunk in chunks]
    return ChatResponse(chat_id=chat.id, answer=answer, sources=sources)
