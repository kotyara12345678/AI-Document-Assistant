"""Tests for the authenticated original-file endpoint behind the PDF viewer."""

import uuid
from pathlib import Path

from app.core.config import settings
from app.database.session import SessionLocal
from app.models.document import Document

PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n"
    b"%%EOF"
)


def _seed_document(user_id: int, data: bytes = PDF_BYTES, file_type: str = "pdf") -> int:
    """Create a Document row pointing at a real file on the uploads volume."""
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}.{file_type}"
    filepath = upload_dir / stored_name
    filepath.write_bytes(data)

    db = SessionLocal()
    try:
        doc = Document(
            user_id=user_id,
            filename=stored_name,
            original_filename=f"doc.{file_type}",
            file_type=file_type,
            file_size=len(data),
            filepath=str(filepath),
            content="seed content",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return doc.id
    finally:
        db.close()


def test_owner_can_download_original_file(client, identity):
    doc_id = _seed_document(identity.user_id)

    resp = client.get(f"/api/documents/{doc_id}/file")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/pdf")
    assert resp.content == PDF_BYTES
    assert resp.headers.get("x-content-type-options") == "nosniff"


def test_file_download_is_scoped_to_owner(client, identity):
    doc_id = _seed_document(identity.user_id)
    other = __import__("tests.conftest", fromlist=["_register_user"])._register_user(client)
    other_headers = {"Authorization": f"Bearer {other['token']}"}

    resp = client.get(f"/api/documents/{doc_id}/file", headers=other_headers)
    assert resp.status_code == 404, resp.text


def test_file_download_requires_auth():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        resp = c.get("/api/documents/1/file")
    assert resp.status_code == 401, resp.text