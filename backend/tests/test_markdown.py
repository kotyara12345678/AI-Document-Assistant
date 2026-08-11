"""Markdown (.md) support: upload, verbatim extraction and RAG indexing.

Markdown is treated as a plain-text document whose raw source becomes the
extracted content: the syntax (headings, lists, code, tables) must survive
unchanged for the frontend viewer, so nothing is stripped during extraction.
"""

import uuid

API_PREFIX = "/api"


def _build_markdown(marker: str) -> str:
    return f"""# Проект Атлас

## Введение

Это **жирный** текст и *курсив*, а также [ссылка](https://example.com).

- первый пункт
- второй пункт

1. раз
2. два

> Цитата мудреца.

---

```python
def hello():
    return "world"
```

| Имя | Роль |
| --- | ---- |
| Анна | Инженер |
| Иван | Аналитик |

Секретный маркер: {marker}
"""


def test_upload_markdown_preserves_content_verbatim(client):
    marker = f"MD{uuid.uuid4().hex[:6]}"
    source = _build_markdown(marker)

    resp = client.post(
        f"{API_PREFIX}/documents/upload",
        files={"file": ("atlas.md", source.encode("utf-8"))},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()[0]
    assert data["file_type"] == "md"
    assert data["original_filename"] == "atlas.md"

    content = client.get(f"{API_PREFIX}/documents/{data['id']}/content")
    assert content.status_code == 200, content.text
    body = content.json()

    assert body["file_type"] == "md"
    assert body["content"] == source.strip(), "markdown must be preserved verbatim"
    assert body["content_length"] == len(source.strip())

    for snippet in (
        "# Проект Атлас",
        "**жирный**",
        "```python",
        "def hello():",
        "| Имя | Роль |",
        f"Секретный маркер: {marker}",
    ):
        assert snippet in body["content"]


def test_markdown_enters_rag_pipeline(client):
    marker = f"MDRAG{uuid.uuid4().hex[:6]}"
    source = _build_markdown(marker)

    upload = client.post(
        f"{API_PREFIX}/documents/upload",
        files={"file": ("rag.md", source.encode("utf-8"))},
    )
    assert upload.status_code == 201, upload.text
    doc_id = upload.json()[0]["id"]

    search = client.post(
        f"{API_PREFIX}/search",
        json={"query": f"секретный маркер {marker}", "limit": 5},
    )
    assert search.status_code == 200, search.text
    results = search.json()["results"]
    assert results, "markdown content must be indexed and searchable"
    assert results[0]["document_id"] == doc_id
    assert marker in results[0]["text"]


def test_markdown_with_binary_magic_bytes_rejected(client):
    """A docx pretending to be .md must be caught by the signature check."""
    resp = client.post(
        f"{API_PREFIX}/documents/upload",
        files={"file": ("fake.md", b"PK\x03\x04 not a real markdown")},
    )
    assert resp.status_code == 400, resp.text
