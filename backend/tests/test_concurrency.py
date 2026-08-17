"""Concurrency / light load regression tests.

After a real HTTP run under parallel requests these exercise the shared
resources that used to be the concurrency hot spots: the cached Qdrant client,
the lazy-loaded embedding model, the retrieval thread pool and the search
service wrapper. Any of them crashing under parallel load would break the
single-worker deployment the app is shipped as.

The embedding model is loaded once (double-checked lock) and reused by all
threads; concurrent search requests then hammer the same Qdrant collection.
"""

import threading
import uuid

from app.services import search as search_service
from app.vector import client as vector_client

N_WORKERS = 16
N_ROUNDS = 3


def _upload_with_text(client, filename: str, text: str) -> int:
    resp = client.post(
        "/api/documents/upload",
        files={"file": (filename, text.encode("utf-8"))},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()[0]["id"]


def test_parallel_semantic_search_is_stable_and_safe(client):
    """Many concurrent search requests on one Qdrant client never 500."""
    marker = f"LOAD{uuid.uuid4().hex[:6]}"
    text = (
        f"База знаний по автоматизации и робототехнике {marker}. "
        "Роботы собирают 1000 деталей в час. "
    ) * 40
    doc_id = _upload_with_text(client, "load.txt", text)

    search_resp = client.post(
        "/api/search", json={"query": f"роботы {marker}", "limit": 5}
    )
    assert search_resp.status_code == 200, search_resp.text
    assert any(r["document_id"] == doc_id for r in search_resp.json()["results"]), (
        "baseline search must find the uploaded document"
    )

    barrier = threading.Barrier(N_WORKERS)
    errors: list = []
    ok_counts: list[int] = []

    def _worker() -> None:
        try:
            barrier.wait()
            for _ in range(N_ROUNDS):
                resp = search_service.semantic_search(
                    query=f"производительность роботов {marker}",
                    limit=5,
                    user_id=None,  # filter-free: purely the concurrency harness
                )
                ok_counts.append(len(resp.results))
        except Exception as exc:  # pragma: no cover - harness
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(N_WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent search raised: {errors}"
    assert len(ok_counts) == N_WORKERS * N_ROUNDS, len(ok_counts)
    assert all(isinstance(n, int) and n >= 0 for n in ok_counts)


def test_parallel_upload_indexing_is_idempotent(client):
    """Simultaneous uploads must not corrupt each other's Qdrant points."""
    barrier = threading.Barrier(2)
    counts: list = []

    def _worker(tag: str) -> None:
        try:
            barrier.wait()
            doc_id = _upload_with_text(
                client,
                f"par_{tag}.txt",
                f"Параллельная загрузка номер {tag}. Данные о метеоритах и астероидах. " * 20,
            )
            n = vector_client.document_vector_count(doc_id)
            counts.append((doc_id, n))
        except Exception as exc:  # pragma: no cover - harness
            counts.append(("error", str(exc)))

    workers = [threading.Thread(target=_worker, args=(i,)) for i in range(2)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    assert all(isinstance(c[1], int) and c[1] > 0 for c in counts), counts
    ids = {c[0] for c in counts}
    assert len(ids) == 2, "two uploads must create two distinct documents"