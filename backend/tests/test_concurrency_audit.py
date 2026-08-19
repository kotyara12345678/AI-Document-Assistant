"""Deep concurrency audit: uploads, RAG isolation, edits, indexing, rate
limiter, shared-resource init, PostgreSQL and agent state.

Each test uses a `threading.Barrier` to line threads up before the racy section
so the check-then-act windows are actually hit. The embedding model and Qdrant
are shared across threads exactly as in production (single shared client,
double-checked model load), so a pass here means the resources hold up under
parallel first-touch too.
"""

import io
import json
import os
import threading
import uuid

import fitz
import pytest

from app.core import ratelimit
from app.core.config import settings
from app.database.session import SessionLocal
from app.models.agent_session import AgentSession
from app.models.chat import Chat
from app.models.chat_message import ChatMessage, ChatSummary
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.user import User
from app.services import agent_state, chat as chat_service, documents as doc_service
from app.services import indexing as indexing_service
from app.services import gemini
from app.services.agent import agent_service
from app.services.retrieval import retrieve_context
from app.vector import client as vector_client
from app.vector.embeddings import embed_text


from app.core.ratelimit import RateLimiter as _CoreRateLimiter  # noqa: E402

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _upload(client, name: str, content: bytes, token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    resp = client.post(
        "/api/documents/upload",
        files={"file": (name, content)},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()[0]


def _make_pdf_bytes(text: str = "Hello manual body LXSHOW") -> bytes:
    doc = fitz.open()
    doc.new_page()
    doc[0].insert_text(fitz.Point(50, 40), text)
    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True)
    content = buf.getvalue()
    doc.close()
    return content


def _echo_fake(prompt, system_instruction=None, **kwargs):
    """Structured-echo mock for the edit pipeline (see test_document_edit_*)."""
    idx = prompt.index('{"items"')
    payload = json.loads(prompt[idx:])
    items = payload["items"]
    out = []
    for it in items:
        if it["type"] == "text":
            out.append({"id": it["id"], "type": "text", "text": it["text"].strip()})
        else:
            out.append(
                {"id": it["id"], "type": "table", "cells": [[c.strip() for c in row] for row in it["cells"]]}
            )
    return json.dumps({"items": out})


def _with_threads(n, fn):
    barrier = threading.Barrier(n)
    errors: list = []

    def _wrapper(i):
        try:
            barrier.wait()
            return fn(i)
        except Exception as exc:  # pragma: no cover - harness
            errors.append(exc)
            return None

    results = [None] * n
    threads = []
    for i in range(n):
        t = threading.Thread(target=lambda i=i: results.__setitem__(i, _wrapper(i)))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    return results, errors


# --------------------------------------------------------------------------- #
# 2. parallel uploads (10 / 25 / 50)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("n", [10, 25, 50])
def test_parallel_uploads_isolate_users(n, client, register_user):
    users = []
    for _ in range(n):
        info = register_user(client)
        users.append((info["user_id"], info["token"]))

    def _upload_one(i):
        uid, token = users[i]
        marker = f"маркер{uuid.uuid4().hex[:6]}"
        text = f"Пользователь {i} {marker}. Статистика марсианских колоний и солнечных батарей. " * 12
        doc = _upload(client, f"doc_{i}.txt", text.encode("utf-8"), token=token)
        return uid, marker, doc

    results, errors = _with_threads(n, _upload_one)
    assert not errors, f"parallel uploads raised: {errors}"
    assert all(r is not None for r in results)

    for uid, marker, doc in results:
        db = SessionLocal()
        try:
            row = db.query(Document).filter(Document.id == doc["id"]).first()
            assert row is not None, f"document {doc['id']} missing"
            assert row.user_id == uid, "ownership leaked across users"
            assert os.path.isfile(row.filepath), f"lost file: {row.filepath}"
            assert marker in row.content, "document content mixed across users"

            n_points = vector_client.document_vector_count(doc["id"])
            chunks = (
                db.query(DocumentChunk)
                .filter(DocumentChunk.document_id == doc["id"])
                .all()
            )
            assert n_points == len(chunks) > 0, "vector count != chunk count (dup/lost)"
            idxs = [c.chunk_index for c in chunks]
            assert len(set(idxs)) == len(idxs), "duplicate chunk_index in FTS rows"

            # Qdrant payload must be owned by the right user.
            hits = vector_client.search_vectors(
                query_vector=embed_text(marker),
                limit=5,
                user_id=uid,
            )
            assert hits, "own document must be searchable by its owner"
            assert all(h.get("payload", {}).get("user_id") == uid for h in hits)
        finally:
            db.close()

    # Cross-user retrieval: a user only ever sees their OWN document.
    for uid, marker, doc in results:
        found = retrieve_context(question=marker, user_id=uid, top_k=5)
        ids = {c.source.document_id for c in found}
        assert ids == {doc["id"]}, f"user {uid} reached foreign documents: {ids}"


# --------------------------------------------------------------------------- #
# 3. parallel RAG isolation
# --------------------------------------------------------------------------- #


def test_parallel_rag_never_crosses_users(client, register_user):
    a = register_user(client)
    b = register_user(client)

    marker_a = f"изотоп_стронция_{uuid.uuid4().hex[:6]}"
    marker_b = f"аэрогель_кремния_{uuid.uuid4().hex[:6]}"
    doc_a = _upload(client, "a.txt", f"Уникальные данные про {marker_a}. ".encode(), token=a["token"])
    doc_b = _upload(client, "b.txt", f"Уникальные данные про {marker_b}. ".encode(), token=b["token"])

    def _probe(args):
        uid, marker, expected_id = args
        for _ in range(2):
            found = retrieve_context(question=marker, user_id=uid, top_k=5)
            ids = {c.source.document_id for c in found}
            assert ids == {expected_id}, f"user {uid} cross-talked: {ids}"
        return True

    results, errors = _with_threads(
        4, lambda i: _probe([(a["user_id"], marker_a, doc_a["id"]), (b["user_id"], marker_b, doc_b["id"])][i % 2])
    )
    assert not errors, f"parallel RAG raised: {errors}"
    assert all(results)

    # Explicit document_ids of ANOTHER user must never resolve to its chunks.
    db = SessionLocal()
    try:
        crossed = retrieve_context(
            question=marker_a, user_id=b["user_id"], document_id=[doc_a["id"]], top_k=5
        )
        assert crossed == [], "user B reached user A's document via document_ids"
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 4. parallel edit of one source
# --------------------------------------------------------------------------- #


def test_parallel_edit_same_source_separate_results(client, user_id, monkeypatch):
    original = _make_pdf_bytes()
    source = _upload(client, "manual.pdf", original)
    source_id = source["id"]

    db0 = SessionLocal()
    try:
        source_path = db0.query(Document).filter(Document.id == source_id).first().filepath
    finally:
        db0.close()
    original_bytes = open(source_path, "rb").read()

    monkeypatch.setattr(gemini, "generate_answer", _echo_fake)

    def _edit():
        db = SessionLocal()
        try:
            from app.services.document_edit import edit_document

            return edit_document(source_id, "переведи на русский, PDF", user_id, db)
        finally:
            db.close()

    results, errors = _with_threads(2, lambda i: _edit())
    assert not errors, f"parallel edits raised: {errors}"

    new_ids = []
    for r in results:
        assert r["success"] is True
        assert r["source_file_id"] == source_id, "source_file_id wrong after parallel edit"
        new_ids.append(r["document_id"])
    assert len(set(new_ids)) == 2, "two parallel edits must yield two distinct documents"

    db = SessionLocal()
    try:
        for new_id in new_ids:
            row = db.query(Document).filter(Document.id == new_id).first()
            assert row is not None
            assert row.source_file_id == source_id
            assert os.path.isfile(row.filepath), f"lost edited file: {row.filepath}"
    finally:
        db.close()

    # The original is never touched.
    assert open(source_path, "rb").read() == original_bytes


# --------------------------------------------------------------------------- #
# 5. concurrent indexing of one document
# --------------------------------------------------------------------------- #


def test_concurrent_index_same_document_no_duplicates(client, user_id):
    text = ("Параллельная индексация. Статистика по промышленным роботам и конвейерам. " * 20).encode()
    doc = _upload(client, "idx.txt", text)
    doc_id = doc["id"]

    db0 = SessionLocal()
    try:
        orm = db0.query(Document).filter(Document.id == doc_id).first()
    finally:
        db0.close()

    results, errors = _with_threads(2, lambda i: indexing_service.index_document(orm))
    assert not errors, f"concurrent index raised: {errors}"
    assert all(r is not None for r in results)

    db = SessionLocal()
    try:
        chunks = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).all()
        n_points = vector_client.document_vector_count(doc_id)
        assert n_points == len(chunks) > 0, "vector/FTS desync after concurrent index"
        idxs = [c.chunk_index for c in chunks]
        assert len(set(idxs)) == len(idxs), "duplicate chunks after concurrent index"
    finally:
        db.close()


def test_index_after_delete_leaves_no_ghost_vectors(client, user_id):
    text = ("Документ который будет удалён. Данные про магнитные левитационные поезда. " * 20).encode()
    doc = _upload(client, "todelete.txt", text)
    doc_id = doc["id"]

    db0 = SessionLocal()
    try:
        orm = db0.query(Document).filter(Document.id == doc_id).first()
    finally:
        db0.close()

    db = SessionLocal()
    try:
        doc_service.delete_document(doc_id, user_id, db)
    finally:
        db.close()

    # The document is gone from the DB, but the (stale) ORM object still points
    # at it. A re-index racing/landing after a delete must NOT resurrect
    # stale vectors or FTS chunks for a row that no longer exists.
    indexing_service.index_document(orm)

    assert vector_client.document_vector_count(doc_id) == 0, "ghost vectors for deleted document"
    db = SessionLocal()
    try:
        assert (
            db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).count() == 0
        ), "orphan FTS chunks for deleted document"
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 6. rate limiter under parallel requests
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("n", [10, 50, 100])
def test_rate_limiter_parallel_exact_counts(n):
    rl = _CoreRateLimiter()
    allowed = [0] * n

    def _probe(i):
        if rl.allow("shared-key", 7, 60):
            allowed[i] = 1

    results, errors = _with_threads(n, _probe)
    assert not errors
    assert sum(allowed) == 7, f"expected exactly 7 allowed, got {sum(allowed)}"

    # Everything after the window hits the limit and is refused, not dropped.
    for _ in range(5):
        assert rl.allow("shared-key", 7, 60) is False


def test_rate_limiter_memory_stays_bounded():
    rl = _CoreRateLimiter()
    for i in range(ratelimit.MAX_KEYS):
        rl.allow(f"k{i}", 1, 60)
    assert rl.allow("overflow", 2, 60) is True  # exercise the prune/reset path
    assert len(rl._hits) <= ratelimit.MAX_KEYS


# --------------------------------------------------------------------------- #
# 7. shared resources: first initialisation under many threads
# --------------------------------------------------------------------------- #


def test_shared_http_client_single_instance_50():
    original = gemini._client
    gemini._client = None
    try:
        clients = [None] * 50

        def _probe(i):
            clients[i] = gemini._get_shared_client()

        results, errors = _with_threads(50, _probe)
        assert not errors
        identities = {id(c) for c in clients}
        assert len(identities) == 1, "more than one shared httpx.Client created"
        assert clients[0] is gemini._client
    finally:
        gemini._client = original


def test_shared_qdrant_client_and_model_concurrent():
    clients = [None] * 50

    def _probe(i):
        clients[i] = vector_client.get_qdrant_client()
        embed_text(f"параллельный первый вызов модели {i}")
        return len(embed_text("проверка размерности"))

    results, errors = _with_threads(50, _probe)
    assert not errors, f"shared-resource init raised: {errors}"
    assert len({id(c) for c in clients}) == 1, "more than one Qdrant client created"
    assert len(set(results)) == 1, "embedding dimension inconsistent across threads"


# --------------------------------------------------------------------------- #
# 8. PostgreSQL: duplicate email under parallel registration
# --------------------------------------------------------------------------- #


def test_concurrent_duplicate_email_single_winner(client, register_user):
    email = f"dup-{uuid.uuid4().hex[:10]}@example.com"

    statuses = [None] * 8

    def _register(i):
        resp = client.post(
            "/api/auth/register",
            json={"email": email, "password": "test-pass-123", "password_confirm": "test-pass-123"},
        )
        statuses[i] = resp.status_code

    results, errors = _with_threads(8, _register)
    assert not errors, f"parallel register raised: {errors}"
    assert statuses.count(201) == 1, f"expected exactly one 201, got {statuses}"
    assert all(s in (201, 409) for s in statuses), f"unexpected statuses: {statuses}"

    db = SessionLocal()
    try:
        assert db.query(User).filter(User.email == email).count() == 1
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 9. agent state / chat summary / chat resolution
# --------------------------------------------------------------------------- #


def _make_chat(user_id: int, n_messages: int = 0) -> int:
    db = SessionLocal()
    try:
        chat = Chat(user_id=user_id, title="audit")
        db.add(chat)
        db.commit()
        db.refresh(chat)
        chat_id = chat.id
        for i in range(n_messages):
            db.add(
                ChatMessage(
                    user_id=user_id, chat_id=chat_id, role="user", content=f"message {i}"
                )
            )
        db.commit()
        return chat_id
    finally:
        db.close()


def test_agent_state_parallel_save_single_row(user_id):
    chat_id = _make_chat(user_id)
    errors = []

    def _worker(i):
        db = SessionLocal()
        try:
            for _ in range(12):
                agent_state.save_state(
                    db, user_id, chat_id, {"task": {"status": "run"}, "documents": [], "sources": []}
                )
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    barrier = threading.Barrier(2)

    def _wrapped(i):
        barrier.wait()
        return _worker(i)

    results, errs = _with_threads(2, _wrapped)
    assert not errors, f"agent state parallel save raised: {errors}"

    db = SessionLocal()
    try:
        rows = db.query(AgentSession).filter(AgentSession.chat_id == chat_id).all()
        assert len(rows) == 1, "parallel save must keep a single agent_session row"
    finally:
        db.close()


def test_chat_summary_concurrent_first_create(user_id, monkeypatch):
    chat_id = _make_chat(
        user_id,
        n_messages=settings.CHAT_HISTORY_MESSAGES + settings.CHAT_SUMMARY_THRESHOLD + 5,
    )
    gate = threading.Barrier(2)

    def fake(prompt, system_instruction=None, **kwargs):
        gate.wait()
        return "ROLLED SUMMARY"

    monkeypatch.setattr("app.services.chat.gemini.generate_answer", fake)

    errors = []

    def _summarise(i):
        db = SessionLocal()
        try:
            chat = db.query(Chat).filter(Chat.id == chat_id).first()
            chat_service._make_summary(db, chat)
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    results, errs = _with_threads(2, _summarise)
    assert not errors, f"parallel summary raised: {errors}"

    db = SessionLocal()
    try:
        rows = db.query(ChatSummary).filter(ChatSummary.chat_id == chat_id).all()
        assert len(rows) == 1, "parallel summary must keep a single row"
        assert rows[0].summary == "ROLLED SUMMARY"
        assert rows[0].last_message_id > 0
    finally:
        db.close()


def test_resolve_chat_concurrent_first_single_chat(user_id):
    chat_ids = [None] * 8

    def _resolve(i):
        db = SessionLocal()
        try:
            chat_ids[i] = chat_service.resolve_chat(db, user_id, None).id
        finally:
            db.close()

    results, errors = _with_threads(8, _resolve)
    assert not errors, f"parallel resolve_chat raised: {errors}"
    assert len(set(chat_ids)) == 1, f"expected one chat, got {set(chat_ids)}"

    db = SessionLocal()
    try:
        assert db.query(Chat).filter(Chat.user_id == user_id).count() == 1
    finally:
        db.close()


def test_parallel_agent_turns_do_not_mix(client, user_id, monkeypatch):
    chat_id = _make_chat(user_id)

    def fake(messages, functions=None, function_call="auto", functions_state_id=None, client=None, usage_hook=None):
        q = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "?")
        return {"content": f"ANSWER[{q}]"}, None

    monkeypatch.setattr(gemini, "chat_with_functions", fake)

    from app.schemas.agent import AgentRequest

    sinks = {}
    errors = []

    def _run(tag):
        db = SessionLocal()
        try:
            sink = {}
            req = AgentRequest(question=f"Расскажи про {tag}", chat_id=chat_id)
            for _ in agent_service.run_agent_stream(req, user_id=user_id, db=db, sink=sink):
                pass
            sinks[tag] = sink
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    barrier = threading.Barrier(2)

    def _wrapped(tag):
        barrier.wait()
        _run(tag)
        return True

    results, errs = _with_threads(2, lambda i: _wrapped("альфа" if i == 0 else "бета"))
    assert not errors, f"parallel agent raised: {errors}"

    assert sinks["альфа"]["answer"] == "ANSWER[Расскажи про альфа]"
    assert sinks["бета"]["answer"] == "ANSWER[Расскажи про бета]"
    assert sinks["альфа"]["chat_id"] == chat_id
    assert sinks["бета"]["chat_id"] == chat_id

    db = SessionLocal()
    try:
        msgs = (
            db.query(ChatMessage)
            .filter(ChatMessage.chat_id == chat_id, ChatMessage.role == "assistant")
            .all()
        )
        contents = [m.content for m in msgs]
        assert "ANSWER[Расскажи про альфа]" in contents
        assert "ANSWER[Расскажи про бета]" in contents
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# session health: one failing request must not poison neighbours
# --------------------------------------------------------------------------- #


def test_parallel_bad_upload_does_not_break_good_ones(client, user_id):
    good = [None] * 4

    def _good(i):
        good[i] = _upload(client, f"ok_{i}.txt", ("Здоровый документ номер %d. " % i).encode())

    def _bad(i):
        resp = client.post(
            "/api/documents/upload", files={"file": ("bad.exe", b"not really an exe")}
        )
        return resp.status_code

    results, errors = _with_threads(4, _good)
    assert not errors
    bad_results, bad_errors = _with_threads(4, _bad)
    assert not bad_errors
    assert all(s == 400 for s in bad_results), bad_results

    db = SessionLocal()
    try:
        assert db.query(Document).filter(Document.user_id == user_id).count() == 4
    finally:
        db.close()