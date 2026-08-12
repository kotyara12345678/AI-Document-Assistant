"""Deterministic GigaChat mock for CI/E2E runs.

The backend talks to GigaChat over plain HTTP (OAuth token + OpenAI-compatible
``/chat/completions`` with GigaChat's native ``functions`` protocol). This
server speaks exactly that wire protocol so the REAL backend can be exercised
end-to-end without ever touching the real GigaChat API and without any real
credentials.

Behaviour:

* ``POST /api/v2/oauth`` -> a fake access token.
* ``POST /api/v1/chat/completions``:
    - a plain completion whose system prompt contains ``needs_metadata``
      (the chat metadata classifier) returns a JSON decision;
    - a plain completion returns a canned grounded answer;
    - a completion with ``functions`` drives the agent loop through a fixed
      script: ``search_documents -> read_document -> create_document -> answer``
      keyed by ``functions_state_id``, reading the real tool results the
      backend echoes back.

Run it with a bare ``python`` — no third-party packages needed.
"""

import json
import re
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

HOST = "0.0.0.0"
PORT = 18000

METADATA_MARKER = "needs_metadata"

AGENT_SCRIPT = {
    None: ("search_documents", "s1"),
    "s1": ("read_document", "s2"),
    "s2": ("create_document", "s3"),
    "s3": (None, None),  # final plain answer
}


def _json_args(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _last_user_message(messages) -> str:
    for message in reversed(messages):
        if message.get("role") == "user" and message.get("content"):
            return message["content"]
    return "default query"


def _last_function_result(messages, name: str) -> dict | None:
    for message in reversed(messages):
        if message.get("role") == "function" and message.get("name") == name:
            content = message.get("content") or ""
            try:
                return json.loads(content)
            except ValueError:
                return None
    return None


def _build_agent_call(body: dict) -> dict:
    state_id = body.get("functions_state_id")
    name, next_state = AGENT_SCRIPT.get(state_id, (None, None))

    if name is None:
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Договор создан и сохранён (CI-мок).",
                    }
                }
            ],
            "functions_state_id": None,
        }

    if name == "search_documents":
        query = _last_user_message(body.get("messages", []))[:200]
        call = {"name": name, "arguments": _json_args({"query": query})}
    elif name == "read_document":
        hits = _last_function_result(body.get("messages", []), "search_documents")
        document_id = 0
        if isinstance(hits, list) and hits and isinstance(hits[0], dict):
            document_id = hits[0].get("document_id") or 0
        call = {"name": name, "arguments": _json_args({"document_id": document_id})}
    else:  # create_document
        spec = {
            "title": "Трудовой договор (CI smoke)",
            "blocks": [
                {"type": "heading", "level": 1, "text": "1. Общие положения"},
                {
                    "type": "paragraph",
                    "text": "Договор создан в рамках автоматического CI smoke-теста.",
                },
            ],
        }
        call = {
            "name": name,
            "arguments": _json_args(
                {"document_spec": spec, "output_format": "docx"}
            ),
        }

    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "function_call": call,
                }
            }
        ],
        "functions_state_id": next_state,
    }


def _build_plain_call(body: dict) -> dict:
    messages = body.get("messages", [])
    system_text = " ".join(
        message.get("content") or ""
        for message in messages
        if message.get("role") == "system"
    )
    if METADATA_MARKER in system_text:
        content = json.dumps(
            {"needs_metadata": False, "fields": [], "target_filename": None},
            ensure_ascii=False,
        )
    else:
        content = "Ответ из CI-мока: бюджет проекта составляет 900000 рублей."
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "functions_state_id": None,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if urlparse(self.path).path == "/":
            self._send_json(200, {"service": "gigachat-mock", "status": "ok"})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")
        path = urlparse(self.path).path
        body: dict = {}
        if raw:
            if "application/x-www-form-urlencoded" in content_type:
                # GigaChat's /oauth endpoint is form-encoded (scope=...).
                body = dict(parse_qsl(raw.decode("utf-8")))
            else:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except ValueError:
                    self._send_json(400, {"error": "invalid json"})
                    return

        if path.endswith("/oauth"):
            self._send_json(
                200,
                {
                    "access_token": "ci-mock-token",
                    "expires_at": 0,
                    "expires_in": 3600,
                },
            )
            return
        if path.endswith("/chat/completions"):
            if body.get("functions"):
                self._send_json(200, _build_agent_call(body))
            else:
                self._send_json(200, _build_plain_call(body))
            return

        self._send_json(404, {"error": f"unknown endpoint {path}"})

    def log_message(self, format, *args):  # noqa: A002
        print(f"[mock-gigachat] {self.address_string()} {format % args}", flush=True)


if __name__ == "__main__":
    print(f"[mock-gigachat] listening on {HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
