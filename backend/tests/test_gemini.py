"""Unit tests for the LLM service (GigaChat chat completions). No network calls."""

import types

import pytest

from app.services.gemini import GeminiError, generate_answer

KEY = "test-key"


class _FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self
            )

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, data=None, status_code=200, error=None):
        self._data = data
        self._status_code = status_code
        self._error = error
        self.last_kwargs = None

    def post(self, url, headers=None, json=None):
        self.last_kwargs = {"url": url, "headers": headers, "json": json}
        if self._error:
            raise self._error
        return _FakeResponse(self._data, status_code=self._status_code)


def _patch_settings(monkeypatch):
    fake = types.SimpleNamespace(
        GIGACHAT_CLIENT_ID="client-id",
        GIGACHAT_CLIENT_SECRET="client-secret",
        GIGACHAT_SCOPE="GIGACHAT_API_PERS",
        GIGACHAT_MODEL="GigaChat-Max",
        GIGACHAT_TEMPERATURE=0.2,
        GIGACHAT_MAX_TOKENS=2048,
        GIGACHAT_TIMEOUT=60.0,
        GIGACHAT_BASE_URL="https://gigachat.devices.sberbank.ru/api/v1",
        GIGACHAT_TOKEN_TTL_SECONDS=1800,
    )
    monkeypatch.setattr("app.services.gemini.settings", fake)
    monkeypatch.setattr("app.services.gemini._get_access_token", lambda client: KEY)


def test_missing_credentials_raises(monkeypatch):
    _patch_settings(monkeypatch)
    fake = types.SimpleNamespace(
        GIGACHAT_CLIENT_ID=None,
        GIGACHAT_CLIENT_SECRET=None,
    )
    monkeypatch.setattr("app.services.gemini.settings", fake)
    from app.services.gemini import _basic_auth_header

    with pytest.raises(GeminiError, match="GIGACHAT_CLIENT_ID"):
        _basic_auth_header()


def test_generate_answer_returns_text(monkeypatch):
    _patch_settings(monkeypatch)
    fake = _FakeClient(data={"choices": [{"message": {"content": "  ответ  "}}]})
    result = generate_answer("question?", client=fake)
    assert result == "ответ"


def test_generate_answer_strips_whitespace(monkeypatch):
    _patch_settings(monkeypatch)
    fake = _FakeClient(data={"choices": [{"message": {"content": "\n  trimmed  \n"}}]})
    result = generate_answer("q", client=fake)
    assert result == "trimmed"


def test_upstream_error_wrapped(monkeypatch):
    _patch_settings(monkeypatch)
    fake = _FakeClient(error=RuntimeError("boom"))
    with pytest.raises(GeminiError, match="boom"):
        generate_answer("q", client=fake)


def test_http_error_wrapped(monkeypatch):
    _patch_settings(monkeypatch)
    fake = _FakeClient(data={}, status_code=429)
    with pytest.raises(GeminiError, match="GigaChat request failed"):
        generate_answer("q", client=fake)


def test_empty_response_raises(monkeypatch):
    _patch_settings(monkeypatch)
    fake = _FakeClient(data={"choices": [{"message": {"content": ""}}]})
    with pytest.raises(GeminiError, match="empty response"):
        generate_answer("q", client=fake)


def test_missing_content_raises(monkeypatch):
    _patch_settings(monkeypatch)
    fake = _FakeClient(data={"choices": []})
    with pytest.raises(GeminiError, match="empty response"):
        generate_answer("q", client=fake)


def test_passes_model_and_messages(monkeypatch):
    _patch_settings(monkeypatch)
    fake = _FakeClient(data={"choices": [{"message": {"content": "ok"}}]})
    generate_answer("hello", system_instruction="be nice", client=fake)
    payload = fake.last_kwargs["json"]
    assert payload["model"] == "GigaChat-Max"
    assert payload["messages"] == [
        {"role": "system", "content": "be nice"},
        {"role": "user", "content": "hello"},
    ]
    assert fake.last_kwargs["headers"]["Authorization"] == f"Bearer {KEY}"


def test_passes_history_and_summary(monkeypatch):
    _patch_settings(monkeypatch)
    fake = _FakeClient(data={"choices": [{"message": {"content": "ok"}}]})
    history = [
        {"role": "user", "content": "earlier q"},
        {"role": "assistant", "content": "earlier a"},
    ]
    generate_answer(
        "hello",
        system_instruction="be nice",
        history=history,
        summary="old turns rolled up",
        client=fake,
    )
    payload = fake.last_kwargs["json"]
    assert payload["messages"] == [
        {"role": "system", "content": "be nice"},
        {
            "role": "system",
            "content": "Summary of the earlier conversation:\nold turns rolled up",
        },
        {"role": "user", "content": "earlier q"},
        {"role": "assistant", "content": "earlier a"},
        {"role": "user", "content": "hello"},
    ]
