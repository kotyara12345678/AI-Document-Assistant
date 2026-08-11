"""OpenDocument Text (.odt) support: upload, extraction and RAG indexing.

An ODT is a ZIP container whose text lives in content.xml. Tests build a
minimal (but valid) ODT in-memory so no fixture files are needed.
"""

import io
import uuid
import zipfile

API_PREFIX = "/api"


def _make_odt_bytes(marker: str) -> bytes:
    content_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
  office:version="1.2">
  <office:body><office:text>
    <text:h text:outline-level="1">Заголовок ODT</text:h>
    <text:p>Первый абзац с обычным текстом.</text:p>
    <text:list>
      <text:list-item><text:p>Пункт один</text:p></text:list-item>
      <text:list-item><text:p>Пункт два</text:p></text:list-item>
    </text:list>
    <text:p>Секретный маркер: {marker}</text:p>
  </office:text></office:body>
</office:document-content>"""
    buf = io.BytesIO()
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<manifest:manifest '
        'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
        'manifest:version="1.2">'
        '<manifest:file-entry manifest:full-path="/" manifest:version="1.2" '
        'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="mimetype" '
        'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
        "</manifest:manifest>"
    )
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", b"application/vnd.oasis.opendocument.text")
        zf.writestr("META-INF/manifest.xml", manifest.encode("utf-8"))
        zf.writestr("content.xml", content_xml.encode("utf-8"))
    return buf.getvalue()


def test_upload_odt_extracts_text(client):
    marker = f"ODT{uuid.uuid4().hex[:6]}"
    data = _make_odt_bytes(marker)

    resp = client.post(
        f"{API_PREFIX}/documents/upload",
        files={"file": ("report.odt", data)},
    )
    assert resp.status_code == 201, resp.text
    doc = resp.json()[0]
    assert doc["file_type"] == "odt"
    assert doc["original_filename"] == "report.odt"

    content = client.get(f"{API_PREFIX}/documents/{doc['id']}/content")
    assert content.status_code == 200, content.text
    body = content.json()
    assert body["file_type"] == "odt"

    for snippet in ("Заголовок ODT", "Первый абзац", "Пункт один", "Пункт два", f"маркер: {marker}"):
        assert snippet in body["content"], f"missing: {snippet!r}"
    assert body["content_length"] == len(body["content"])


def test_odt_enters_rag_pipeline(client):
    marker = f"ODTRAG{uuid.uuid4().hex[:6]}"
    data = _make_odt_bytes(marker)

    upload = client.post(
        f"{API_PREFIX}/documents/upload",
        files={"file": ("rag.odt", data)},
    )
    assert upload.status_code == 201, upload.text
    doc_id = upload.json()[0]["id"]

    search = client.post(
        f"{API_PREFIX}/search",
        json={"query": f"секретный маркер {marker}", "limit": 5},
    )
    assert search.status_code == 200, search.text
    results = search.json()["results"]
    assert results, "ODT text must be indexed and searchable"
    assert results[0]["document_id"] == doc_id
    assert marker in results[0]["text"]


def test_odt_with_wrong_magic_bytes_rejected(client):
    """A docx pretending to be .odt is caught by the ZIP signature check."""
    resp = client.post(
        f"{API_PREFIX}/documents/upload",
        files={"file": ("fake.odt", b"PK\x03\x04 not a real odt")},
    )
    assert resp.status_code == 400, resp.text
