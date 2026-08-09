"""LLM client via Sber GigaChat API (OAuth 2.0 client-credentials flow +
OpenAI-compatible completions).

Keeps the same module name, GeminiError, and generate_answer(...) interface,
so the rest of the project (chat service, routes) does not need any changes.

Authentication uses client ID / client secret: the Authorization header is
"Basic base64(client_id:client_secret)". The access token is obtained from
/api/v2/oauth, cached in memory, and refreshed every GIGACHAT_TOKEN_TTL_SECONDS
(default 1800 s = 30 min).
"""

import base64
import logging
import threading
import time
import uuid

import httpx

from app.core.config import settings

logger = logging.getLogger("app.gemini")

AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"

# In-memory token cache: {"token": str, "issued_at": float (unix ts), ...}
_token_lock = threading.Lock()
_token: str | None = None
_token_issued_at: float = 0.0


class GeminiError(RuntimeError):
    """Raised when the LLM call fails."""


def _chat_url() -> str:
    base = settings.GIGACHAT_BASE_URL.rstrip("/")
    return f"{base}/chat/completions"


def _basic_auth_header() -> str:
    if not settings.GIGACHAT_CLIENT_ID or not settings.GIGACHAT_CLIENT_SECRET:
        raise GeminiError("GIGACHAT_CLIENT_ID / GIGACHAT_CLIENT_SECRET are not configured")
    credentials = f"{settings.GIGACHAT_CLIENT_ID}:{settings.GIGACHAT_CLIENT_SECRET}"
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def _create_client() -> httpx.Client:
    return httpx.Client(timeout=settings.GIGACHAT_TIMEOUT)


def _fetch_access_token(client: httpx.Client) -> str:
    """Request a fresh access token via the OAuth client-credentials flow."""
    try:
        response = client.post(
            AUTH_URL,
            headers={
                "Authorization": _basic_auth_header(),
                "RqUID": str(uuid.uuid4()),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={"scope": settings.GIGACHAT_SCOPE},
        )
        response.raise_for_status()
        data = response.json()
    except GeminiError:
        raise
    except Exception as exc:
        logger.exception("GigaChat OAuth token request failed")
        raise GeminiError(f"GigaChat OAuth token request failed: {exc}") from exc

    token = data.get("access_token")
    if not token:
        raise GeminiError("GigaChat OAuth response did not contain access_token")

    global _token, _token_issued_at
    _token = token
    _token_issued_at = time.time()
    return token


def _get_access_token(client: httpx.Client) -> str:
    """Return a cached token, fetching a new one every TTL seconds."""
    global _token, _token_issued_at
    now = time.time()
    with _token_lock:
        if _token and (now - _token_issued_at) < settings.GIGACHAT_TOKEN_TTL_SECONDS:
            return _token
        return _fetch_access_token(client)


def _build_messages(
    prompt: str,
    system_instruction: str | None,
    history: list[dict[str, str]] | None = None,
    summary: str | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    if summary:
        messages.append(
            {"role": "system", "content": f"Summary of the earlier conversation:\n{summary}"}
        )
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})
    return messages


def generate_answer(
    prompt: str,
    system_instruction: str | None = None,
    client=None,
    history: list[dict[str, str]] | None = None,
    summary: str | None = None,
) -> str:
    """Send a prompt to GigaChat and return the text answer.

    `history` carries the most recent turns as [{"role", "content"}] messages
    and `summary` is a compact rollup of older turns; together they provide
    conversational context without sending the full history.

    Raises GeminiError on missing credentials, OAuth failure, or upstream error.
    """
    client = client or _create_client()
    token = _get_access_token(client)

    payload = {
        "model": settings.GIGACHAT_MODEL,
        "temperature": settings.GIGACHAT_TEMPERATURE,
        "max_tokens": settings.GIGACHAT_MAX_TOKENS,
        "messages": _build_messages(prompt, system_instruction, history, summary),
    }

    try:
        response = client.post(
            _chat_url(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.exception("GigaChat request failed")
        raise GeminiError(f"GigaChat request failed: {exc}") from exc

    try:
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiError("GigaChat returned an empty response") from exc

    if not answer:
        raise GeminiError("GigaChat returned an empty response")

    return answer.strip()
