"""Unit tests for the LLM service (GigaChat chat completions). No network calls."""

import base64
import socket
import ssl
import types
import uuid

import httpx
import pytest

from app.services.gemini import (
    GeminiError,
    _classify_transport_error,
    _create_client,
    _fetch_access_token,
    chat_with_functions,
    generate_answer,
)

KEY = "test-key"


class _FakeResponse:
    def __init__(self, data, status_code=200, text=""):
        self._data = data
        self.status_code = status_code
        self.text = text
        self.url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

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


def test_chat_with_functions_returns_message_and_state(monkeypatch):
    _patch_settings(monkeypatch)
    message = {
        "role": "assistant",
        "content": None,
        "function_call": {"name": "search_documents", "arguments": {"query": "q"}},
        "functions_state_id": "state-1",
    }
    fake = _FakeClient(data={"choices": [{"message": message}], "functions_state_id": "state-1"})
    result, state_id = chat_with_functions([{"role": "user", "content": "hi"}], client=fake)
    assert result == message
    assert state_id == "state-1"


def test_chat_with_functions_state_from_message_level(monkeypatch):
    _patch_settings(monkeypatch)
    message = {
        "role": "assistant",
        "content": None,
        "function_call": {"name": "search_documents", "arguments": {"query": "q"}},
    }
    fake = _FakeClient(data={"choices": [{"message": message}]})
    result, state_id = chat_with_functions(
        [{"role": "user", "content": "hi"}],
        functions=[{"name": "search_documents"}],
        functions_state_id="prev-state",
        client=fake,
    )
    assert result == message
    assert state_id is None
    payload = fake.last_kwargs["json"]
    assert payload["functions"] == [{"name": "search_documents"}]
    assert payload["function_call"] == "auto"
    assert payload["functions_state_id"] == "prev-state"


def test_chat_with_functions_plain_without_functions(monkeypatch):
    _patch_settings(monkeypatch)
    fake = _FakeClient(data={"choices": [{"message": {"content": "ok"}}]})
    result, state_id = chat_with_functions([{"role": "user", "content": "hi"}], client=fake)
    assert result["content"] == "ok"
    assert state_id is None
    payload = fake.last_kwargs["json"]
    assert "functions" not in payload
    assert "functions_state_id" not in payload


def test_chat_with_functions_wraps_errors(monkeypatch):
    _patch_settings(monkeypatch)
    fake = _FakeClient(error=RuntimeError("boom"))
    with pytest.raises(GeminiError, match="boom"):
        chat_with_functions([{"role": "user", "content": "hi"}], client=fake)


class _FlakyClient:
    """A client that raises the queued errors on successive ``post`` calls,
    then returns a successful response once the queue is drained."""

    def __init__(self, data, errors, status_code=200):
        self._data = data
        self._errors = list(errors)
        self._status_code = status_code
        self.call_count = 0
        self.last_kwargs = None

    def post(self, url, headers=None, json=None):
        self.call_count += 1
        self.last_kwargs = {"url": url, "headers": headers, "json": json}
        if self._errors:
            raise self._errors.pop(0)
        return _FakeResponse(self._data, status_code=self._status_code)


def test_read_timeout_is_retried_then_succeeds(monkeypatch):
    """One transient ReadTimeout must trigger exactly one retry and still return."""
    import httpx

    _patch_settings(monkeypatch)
    flaky = _FlakyClient(
        data={"choices": [{"message": {"content": "ok"}}]},
        errors=[httpx.ReadTimeout("timed out")],
    )
    assert generate_answer("q", client=flaky) == "ok"
    assert flaky.call_count == 2  # initial attempt + one retry


def test_read_timeout_retry_exhausted_raises_geminierror(monkeypatch):
    """After the single retry is exhausted the original error surfaces as GeminiError."""
    import httpx

    _patch_settings(monkeypatch)
    flaky = _FlakyClient(
        data={"choices": [{"message": {"content": "ok"}}]},
        errors=[httpx.ReadTimeout("t1"), httpx.ReadTimeout("t2")],
    )
    with pytest.raises(GeminiError) as excinfo:
        generate_answer("q", client=flaky)
    # Exactly one retry: two attempts total, never more.
    assert flaky.call_count == 2
    assert "GigaChat request failed" in str(excinfo.value)


def test_http_status_error_is_not_retried(monkeypatch):
    """4xx/5xx are permanent: they must NOT be retried (one attempt only)."""
    _patch_settings(monkeypatch)
    flaky = _FlakyClient(data={}, status_code=429, errors=[])
    with pytest.raises(GeminiError, match="GigaChat request failed"):
        generate_answer("q", client=flaky)
    assert flaky.call_count == 1


def test_connect_error_is_retried_then_succeeds(monkeypatch):
    import httpx

    _patch_settings(monkeypatch)
    flaky = _FlakyClient(
        data={"choices": [{"message": {"content": "ok"}}]},
        errors=[httpx.ConnectError("conn refused")],
    )
    assert generate_answer("q", client=flaky) == "ok"
    assert flaky.call_count == 2


def test_chat_with_functions_http_422_logs_body_and_no_secrets(monkeypatch):
    """HTTP 422 from GigaChat must:

    - log the response body (so we can see the real rejection reason),
    - never leak secrets (client secret / Bearer token) into the log,
    - still raise GeminiError (graceful fallback must keep working).
    """
    import app.services.gemini as gemini_mod

    _patch_settings(monkeypatch)
    error_body = (
        '{"code":422,"message":"Invalid parameter: '
        'functions[0].parameters must be an object schema"}'
    )

    class _FakeResp:
        status_code = 422
        text = error_body
        url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

        def raise_for_status(self):
            import httpx

            raise httpx.HTTPStatusError("422", request=None, response=self)

        def json(self):
            return {}

    class _FakeCli:
        last_kwargs = None

        def post(self, url, headers=None, json=None):
            self.last_kwargs = {"url": url, "headers": headers, "json": json}
            return _FakeResp()

    captured: list[str] = []

    def _spy_error(*args, **kwargs):
        # Reconstruct the message exactly as logging would.
        if args:
            msg = args[0] % args[1:] if len(args) > 1 else args[0]
        else:
            msg = kwargs.get("msg", "")
        captured.append(str(msg))

    monkeypatch.setattr(gemini_mod.logger, "error", _spy_error)

    cli = _FakeCli()
    with pytest.raises(GeminiError) as excinfo:
        chat_with_functions(
            [{"role": "user", "content": "hi"}],
            functions=[{"name": "search_documents", "parameters": {}}],
            client=cli,
        )

    log_text = "\n".join(captured)
    # The real error body reaches the log for diagnosis.
    assert error_body in log_text
    # No secrets leak: the configured client secret and Bearer token are absent.
    assert "client-secret" not in log_text
    assert "Bearer" not in log_text
    # Original failure is preserved as a GeminiError (not masked/swallowed).
    assert isinstance(excinfo.value, GeminiError)
    assert "422" in str(excinfo.value)
    # Sanity: the actual request still carried the auth header (not logged).
    assert cli.last_kwargs["headers"]["Authorization"] == f"Bearer {KEY}"


# --- Googletest-style OAuth regression tests ---


class _OAuthFakeClient:
    """Records the exact OAuth POST; optionally fails on the first try."""

    def __init__(self, token_data=None, error=None):
        self._token_data = token_data if token_data is not None else {}
        self._error = error
        self.calls = []

    def post(self, url, headers=None, data=None, **kwargs):
        self.calls.append({"url": url, "headers": dict(headers or {}), "data": dict(data or {})})
        if self._error is not None:
            raise self._error
        resp = httpx.Response(
            200,
            json=self._token_data,
            request=httpx.Request("POST", url),
        )
        return resp


def _oauth_settings(monkeypatch):
    """Settings object for OAuth: everything _fetch_access_token needs."""
    fake = types.SimpleNamespace(
        GIGACHAT_CLIENT_ID="client-id",
        GIGACHAT_CLIENT_SECRET="client-secret",
        GIGACHAT_AUTH_URL="https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
        GIGACHAT_SCOPE="GIGACHAT_API_PERS",
        GIGACHAT_TIMEOUT=60.0,
        GIGACHAT_READ_TIMEOUT=300.0,
    )
    monkeypatch.setattr("app.services.gemini.settings", fake)
    return fake


def test_oauth_request_shape(monkeypatch):
    """OAuth POST must hit the auth URL with Basic auth, RqUID, form scope."""
    _oauth_settings(monkeypatch)
    client = _OAuthFakeClient(token_data={"access_token": "tok-123", "expires_at": 0})
    token = _fetch_access_token(client)
    assert token == "tok-123"
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    auth = call["headers"]["Authorization"]
    expected = "Basic " + base64.b64encode(b"client-id:client-secret").decode()
    assert auth == expected
    assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert call["headers"]["Accept"] == "application/json"
    assert uuid.UUID(call["headers"]["RqUID"]).version == 4
    assert call["data"] == {"scope": "GIGACHAT_API_PERS"}


def test_oauth_client_timeout_config(monkeypatch):
    """_create_client keeps connect tight and read long (as configured)."""
    _oauth_settings(monkeypatch)
    client = _create_client()
    t = client.timeout
    assert t.connect == 60.0
    assert t.write == 60.0
    assert t.read == 300.0
    assert t.pool == 60.0


@pytest.mark.parametrize(
    "exception,category",
    [
        (httpx.ConnectTimeout("timed out"), "timeout"),
        (httpx.ReadTimeout("timed out"), "timeout"),
        (httpx.ConnectError("conn refused"), "connect_error"),
        (ssl.SSLError("handshake failed"), "tls_error"),
    ],
)
def test_oauth_transport_error_classified(monkeypatch, exception, category):
    fake = _oauth_settings(monkeypatch)
    client = _OAuthFakeClient(error=exception)
    with pytest.raises(GeminiError) as excinfo:
        _fetch_access_token(client)
    assert category in str(excinfo.value)
    assert fake.GIGACHAT_AUTH_URL in str(excinfo.value)


def test_oauth_connection_reset_classified(monkeypatch):
    """A peer RST must be labelled connection_reset, not a generic error."""
    _oauth_settings(monkeypatch)
    cause = ConnectionResetError(104, "Connection reset by peer")
    exc = httpx.ConnectError("connection reset by peer")  # httpx wraps the OSError
    exc.__cause__ = cause
    client = _OAuthFakeClient(error=exc)
    with pytest.raises(GeminiError) as excinfo:
        _fetch_access_token(client)
    assert "connection_reset" in str(excinfo.value)


def test_oauth_dns_error_classified(monkeypatch):
    _oauth_settings(monkeypatch)
    cause = socket.gaierror(-2, "Name or service not known")
    exc = httpx.ConnectError("dns lookup failed")
    exc.__cause__ = cause
    client = _OAuthFakeClient(error=exc)
    with pytest.raises(GeminiError) as excinfo:
        _fetch_access_token(client)
    assert "dns_error" in str(excinfo.value)


def test_oauth_http_error_classified(monkeypatch):
    _oauth_settings(monkeypatch)
    resp = httpx.Response(
        401,
        json={"error": "invalid_client"},
        request=httpx.Request("POST", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"),
    )
    exc = httpx.HTTPStatusError("unauthorized", request=httpx.Request("POST", resp.url), response=resp)
    client = _OAuthFakeClient(error=exc)
    with pytest.raises(GeminiError) as excinfo:
        _fetch_access_token(client)
    assert "HTTP 401" in str(excinfo.value)


def test_oauth_missing_token_raises(monkeypatch):
    _oauth_settings(monkeypatch)
    client = _OAuthFakeClient(token_data={})
    with pytest.raises(GeminiError, match="access_token"):
        _fetch_access_token(client)


def test_classify_transport_error_primitives():
    assert _classify_transport_error(httpx.ConnectTimeout("t"))[0] == "timeout"
    reset = ConnectionResetError(104, "reset")
    assert _classify_transport_error(reset)[0] == "connection_reset"
    assert _classify_transport_error(socket.gaierror(-2, "n"))[0] == "dns_error"
    assert _classify_transport_error(ssl.SSLError("tls"))[0] == "tls_error"
