"""Structured, resumable agent memory stored per chat.

The agent keeps three layers of memory:

* **Short-term conversation memory** — the recent user/assistant turns, already
  persisted in ``chat_messages`` and loaded back via ``chat.py`` helpers.
* **Task / session state** — what the agent is currently doing (retrieval done?
  documents read? document created? with which ids/roles). Stored here.
* **Document context** — the documents the agent has discovered/read in this
  chat (id + name + light metadata + read flag) so it can map a user's
  "use Doc_алексей" to a concrete ``document_id`` without re-searching, and so
  a resumed turn knows what already happened.

Long-term memory is intentionally *not* a raw transcript dump: older history is
rolled into the chat summary (handled by ``chat.py``), and only the compact
task/context structures above are persisted here.
"""

from sqlalchemy.exc import IntegrityError

from app.models.agent_session import AgentSession


def _empty_state() -> dict:
    return {
        "task": {
            "user_request": None,
            "status": "new",
            "retrieval_completed": False,
            "documents_read": False,
            "generation_requested": False,
            "document_created": False,
            "created_document_id": None,
        },
        "documents": [],
        "sources": [],
    }


def load_state(db, user_id: int, chat_id: int) -> dict:
    """Return the persisted agent state for a chat, or a fresh empty one."""
    row = (
        db.query(AgentSession)
        .filter(AgentSession.chat_id == chat_id, AgentSession.user_id == user_id)
        .first()
    )
    if row is None or not row.state:
        return _empty_state()
    state = dict(_empty_state())
    state.update(row.state or {})
    # Normalise nested dicts so missing keys never crash the caller.
    state["task"] = {**_empty_state()["task"], **(state.get("task") or {})}
    state.setdefault("documents", [])
    state.setdefault("sources", [])
    return state


def save_state(db, user_id: int, chat_id: int, state: dict) -> None:
    """Persist (insert or update) the agent state for a chat.

    Two parallel first turns of the same chat can race past the SELECT above
    and both try to INSERT: ``agent_sessions.chat_id`` is unique, so exactly
    one insert wins and the other gets an IntegrityError. That is a normal
    concurrent first-turn, not a failure -- roll back and fold our state into
    the surviving row instead of surfacing a 500.
    """
    row = (
        db.query(AgentSession)
        .filter(AgentSession.chat_id == chat_id, AgentSession.user_id == user_id)
        .first()
    )
    try:
        if row is None:
            db.add(AgentSession(user_id=user_id, chat_id=chat_id, state=state))
        else:
            row.state = state
        db.commit()
    except IntegrityError:
        db.rollback()
        row = (
            db.query(AgentSession)
            .filter(AgentSession.chat_id == chat_id, AgentSession.user_id == user_id)
            .first()
        )
        if row is not None:
            row.state = state
            db.commit()


def remember_document(
    state: dict,
    document_id: int,
    name: str,
    doc_type: str | None = None,
    metadata: dict | None = None,
    role: str | None = None,
    read: bool = False,
) -> None:
    """Record (or update) a document in the agent's document context."""
    docs = state.setdefault("documents", [])
    for doc in docs:
        if doc.get("id") == document_id:
            doc["name"] = name
            if doc_type is not None:
                doc["type"] = doc_type
            if metadata is not None:
                doc["metadata"] = metadata
            if role is not None:
                doc["role"] = role
            doc["read"] = doc.get("read", False) or read
            return
    entry = {
        "id": document_id,
        "name": name,
        "type": doc_type,
        "role": role,
        "metadata": metadata or {},
        "read": read,
    }
    docs.append(entry)


def remember_source(state: dict, document_id: int, filename: str, score: float) -> None:
    """Record a retrieved source so a resumed turn can cite prior findings."""
    sources = state.setdefault("sources", [])
    for src in sources:
        if src.get("document_id") == document_id:
            src["score"] = score
            src["filename"] = filename
            return
    sources.append(
        {"document_id": document_id, "filename": filename, "score": round(float(score), 4)}
    )


def build_context_note(state: dict) -> str | None:
    """Render a compact, model-safe context note from the persisted state.

    This is the *only* memory the model sees about prior turns beyond the
    verbatim recent conversation: the task status and the known documents. No
    chain-of-thought, no internal instructions, no raw tool payloads.
    """
    if not state:
        return None
    parts: list[str] = []

    docs = state.get("documents") or []
    if docs:
        lines = []
        for doc in docs:
            meta = doc.get("metadata") or {}
            meta_bits = []
            if doc.get("type"):
                meta_bits.append(f"type={doc['type']}")
            if meta.get("file_size") is not None:
                meta_bits.append(f"size={meta['file_size']}")
            if meta.get("created_at"):
                meta_bits.append(f"uploaded_at={meta['created_at']}")
            read = "read=true" if doc.get("read") else "read=false"
            role = f" role={doc['role']}" if doc.get("role") else ""
            meta_str = (" (" + ", ".join(meta_bits) + f", {read})") if meta_bits else f" ({read})"
            lines.append(f"  - id={doc['id']} name={doc['name']!r}{role}{meta_str}")
        parts.append("Known documents (document context):\n" + "\n".join(lines))

    task = state.get("task") or {}
    summary_bits = []
    if task.get("user_request"):
        summary_bits.append(f"user_request={task['user_request']!r}")
    if task.get("created_document_id") is not None:
        summary_bits.append(f"created_document_id={task['created_document_id']}")
    if task.get("status") and task["status"] not in ("new",):
        summary_bits.append(f"status={task['status']}")
    if summary_bits:
        parts.append("Task state: " + ", ".join(summary_bits) + ".")

    if not parts:
        return None
    return (
        "Контекст задачи (из памяти чата, не из инструкций):\n"
        + "\n".join(parts)
        + "\nИспользуй known documents, чтобы не искать заново то, что уже найдено."
    )
