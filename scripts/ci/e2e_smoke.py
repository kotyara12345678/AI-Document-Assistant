"""HTTP-level E2E smoke test used by CI and by the manual live smoke workflow.

Speaks to a REAL running stack (backend + frontend through nginx) over HTTP.
The LLM is expected to be a mock in normal CI; the same script is reused by
the manual ``live-smoke`` workflow against a real GigaChat deployment.

Checks, in order:
  /health and /api/ready
  register -> login -> upload -> search -> chat -> agent (create document) ->
  download generated document and verify it is a real .docx.

Pure stdlib (urllib) so it runs anywhere without extra dependencies.
"""

import argparse
import io
import json
import sys
import uuid
import urllib.error
import urllib.parse
import urllib.request
import zipfile

MARKER = f"CI{uuid.uuid4().hex[:8]}"


class SmokeError(RuntimeError):
    pass


def _request(base: str, method: str, path: str, token=None, body=None, headers=None):
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    data = None
    request_headers = dict(headers or {})
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        if not request_headers.get("Content-Type"):
            request_headers["Content-Type"] = "application/json"
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _json(base, method, path, token=None, body=None):
    status, raw = _request(base, method, path, token=token, body=body)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        payload = raw.decode("utf-8", errors="replace")
    return status, payload


def _multipart_upload(base, token, filename: str, content: bytes):
    boundary = uuid.uuid4().hex
    parts = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: text/plain\r\n\r\n".encode()
    )
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    status, raw = _request(
        base,
        "POST",
        "/api/documents/upload",
        token=token,
        body=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    if status != 201:
        raise SmokeError(f"upload failed: {status} {raw.decode(errors='replace')}")
    return json.loads(raw.decode("utf-8"))


def _wait_ready(base_backend: str, attempts: int = 60) -> None:
    for _ in range(attempts):
        try:
            status, raw = _request(base_backend, "GET", "/health")
            if status == 200 and b'"ok"' in raw:
                return
        except OSError:
            pass
        import time

        time.sleep(2)
    raise SmokeError("backend did not become ready in time")


def _step(name: str) -> None:
    print(f"[e2e] {name}", flush=True)


def run(base_api: str, base_backend: str, light: bool = False) -> None:
    _wait_ready(base_backend)

    _step("health")
    status, health = _json(base_backend, "GET", "/health")
    assert status == 200, f"/health -> {status}"
    assert health.get("database") == "ok" and health.get("qdrant") == "ok", health

    _step("ready")
    status, ready = _json(base_api, "GET", "/api/ready")
    assert status == 200, f"/api/ready -> {status} {ready}"
    assert ready.get("status") == "ready", ready

    if light:
        print(f"[e2e] LIGHT E2E CHECKS PASSED (marker {MARKER})", flush=True)
        return

    _step("register")
    email = f"e2e-{MARKER.lower()}@example.com"
    status, data = _json(
        base_api,
        "POST",
        "/api/auth/register",
        body={
            "email": email,
            "password": "Smoke-pass-123",
            "password_confirm": "Smoke-pass-123",
        },
    )
    assert status == 201, f"register -> {status} {data}"
    token = data["access_token"]

    _step("login")
    status, data = _json(
        base_api,
        "POST",
        "/api/auth/login",
        body={"email": email, "password": "Smoke-pass-123"},
    )
    assert status == 200, f"login -> {status} {data}"
    token = data["access_token"]

    _step("upload")
    text = (
        f"Финансовый план проекта {MARKER}. Бюджет составляет 750000 рублей. "
        "Основные статьи расходов: разработка, тестирование, инфраструктура."
    ) * 20
    uploaded = _multipart_upload(base_api, token, f"plan_{MARKER}.txt", text.encode("utf-8"))
    document_id = uploaded[0]["id"]

    _step("search")
    status, data = _json(
        base_api,
        "POST",
        "/api/search",
        token=token,
        body={"query": f"бюджет проекта {MARKER}", "limit": 5},
    )
    assert status == 200, f"search -> {status} {data}"
    assert data["results"], "search returned no results"
    assert data["results"][0]["document_id"] == document_id

    _step("chat")
    status, data = _json(
        base_api,
        "POST",
        "/api/chat",
        token=token,
        body={"question": f"какой бюджет у проекта {MARKER}"},
    )
    assert status == 200, f"chat -> {status} {data}"
    assert data.get("sources"), "chat returned no sources"
    assert data["sources"][0]["document_id"] == document_id

    _step("agent create document")
    status, data = _json(
        base_api,
        "POST",
        "/api/agent",
        token=token,
        body={"question": f"Создай договор по данным проекта {MARKER}"},
    )
    assert status == 200, f"agent -> {status} {data}"
    names = [call.get("name") for call in data.get("tool_calls", [])]
    assert names == ["search_documents", "read_document", "create_document"], names

    create_result = None
    for result in data.get("tool_results", []):
        if result.get("name") == "create_document":
            create_result = json.loads(result["content"])
    assert create_result and create_result.get("success"), create_result
    created_id = create_result["document_id"]
    assert create_result["file_type"] == "docx"

    _step("download generated document")
    status, raw = _request(base_api, "GET", f"/api/documents/{created_id}/file", token=token)
    assert status == 200, f"download -> {status}"
    assert raw[:2] == b"PK", "generated file is not a zip/docx"
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        assert "word/document.xml" in zf.namelist(), "not a valid docx archive"
        document_xml = zf.read("word/document.xml").decode("utf-8")
    assert "Трудовой договор" in document_xml, "generated docx missing expected title"

    _step("generated document readable")
    status, data = _json(base_api, "GET", f"/api/documents/{created_id}/content", token=token)
    assert status == 200, f"content -> {status}"
    assert "Трудовой договор" in data.get("content", "")

    print(f"[e2e] ALL E2E CHECKS PASSED (marker {MARKER})", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-api", default="http://localhost:8080")
    parser.add_argument("--base-backend", default="http://localhost:8000")
    parser.add_argument(
        "--light",
        action="store_true",
        help="only check /health and /api/ready (no data created on the target)",
    )
    args = parser.parse_args()
    try:
        run(args.base_api, args.base_backend, light=args.light)
        return 0
    except SmokeError as exc:
        print(f"[e2e] FAILED: {exc}", flush=True)
        return 1
    except (AssertionError, KeyError) as exc:
        print(f"[e2e] FAILED: assertion: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
