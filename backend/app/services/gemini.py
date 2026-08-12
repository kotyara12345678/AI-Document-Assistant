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
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field

import httpx

from app.core.config import settings

logger = logging.getLogger("app.gemini")

# In-memory token cache: {"token": str, "issued_at": float (unix ts), ...}
_token_lock = threading.Lock()
_token: str | None = None
_token_issued_at: float = 0.0


class GeminiError(RuntimeError):
    """Raised when the LLM call fails."""


# Metadata fields the retrieval layer actually knows how to provide and that
# the classifier may ask for. Anything else is silently dropped so the model
# is never pushed to guess missing metadata.
METADATA_FIELDS_ALLOWED: tuple[str, ...] = (
    "original_filename",
    "file_type",
    "file_size",
    "content_length",
    "created_at",
)


@dataclass
class MetadataDecision:
    """What the LLM decided a question needs from document metadata."""

    needs_metadata: bool = False
    fields: list[str] = field(default_factory=list)
    target_filename: str | None = None


def _extract_json(raw: str) -> dict:
    """Pull the first JSON object out of an LLM response (code fences ok)."""
    cleaned = raw.strip()
    cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object in metadata classifier response")
    return json.loads(cleaned[start : end + 1])


def _parse_metadata_decision(raw: str) -> MetadataDecision:
    data = _extract_json(raw)
    needs = bool(data.get("needs_metadata", False))
    fields = [
        item
        for item in data.get("fields") or []
        if isinstance(item, str) and item in METADATA_FIELDS_ALLOWED
    ]
    target = data.get("target_filename")
    if not isinstance(target, str) or not target.strip():
        target = None
    else:
        target = target.strip()
    return MetadataDecision(needs_metadata=needs, fields=fields, target_filename=target)


def classify_metadata_need(question: str, client=None) -> MetadataDecision:
    """Ask the LLM whether `question` needs document metadata to be answered.

    Returns a MetadataDecision. Any failure (missing credentials, upstream
    error, unparseable JSON) degrades to a no-metadata decision, so the RAG
    pipeline keeps working and never invents metadata.
    """
    if not settings.CHAT_METADATA_CLASSIFIER_ENABLED:
        return MetadataDecision()
    prompt = f"Decide what document metadata this question needs.\n\nQUESTION: {question}"
    try:
        raw = generate_answer(
            prompt,
            system_instruction=settings.CHAT_METADATA_CLASSIFIER_INSTRUCTION,
            client=client,
        )
        return _parse_metadata_decision(raw)
    except Exception:
        logger.exception("Metadata classification failed; defaulting to no metadata")
        return MetadataDecision()


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
            settings.GIGACHAT_AUTH_URL,
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


def chat_completion(
    messages: list[dict],
    client=None,
    tools: list[dict] | None = None,
) -> dict:
    """Send an OpenAI-compatible chat completion and return the assistant message.

    ``messages`` is the full message list (system / user / assistant / tool).
    When ``tools`` is given, GigaChat function calling is enabled: the returned
    message may carry a ``tool_calls`` array that the caller must execute and
    feed back as ``role: "tool"`` messages.

    Raises GeminiError on missing credentials, OAuth failure, or upstream error.
    """
    client = client or _create_client()
    token = _get_access_token(client)

    payload: dict = {
        "model": settings.GIGACHAT_MODEL,
        "temperature": settings.GIGACHAT_TEMPERATURE,
        "max_tokens": settings.GIGACHAT_MAX_TOKENS,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools

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
        return data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiError("GigaChat returned an empty response") from exc


def chat_with_functions(
    messages: list[dict],
    functions: list[dict] | None = None,
    function_call: str = "auto",
    functions_state_id: str | None = None,
    client=None,
) -> tuple[dict, str | None]:
    """Send a chat completion with GigaChat's native function calling enabled.

    The configured ``gigachat.devices.sberbank.ru`` API does not honour the
    OpenAI ``tools`` array: it silently ignores it and answers as if no
    functions existed. GigaChat's native protocol uses ``functions`` +
    ``function_call`` instead, so this method speaks that protocol.

    Returns ``(message, functions_state_id)``. The returned message carries a
    ``function_call`` dict (``{"name", "arguments"}``) when the model wants to
    call a function; the caller must execute it and feed the result back as a
    ``role: "function"`` message. ``functions_state_id`` is an opaque state
    token that GigaChat requires to be echoed back unchanged in every request
    of the same tool-call turn.

    Raises GeminiError on missing credentials, OAuth failure, or upstream error.
    """
    client = client or _create_client()
    token = _get_access_token(client)

    payload: dict = {
        "model": settings.GIGACHAT_MODEL,
        "temperature": settings.GIGACHAT_TEMPERATURE,
        "max_tokens": settings.GIGACHAT_MAX_TOKENS,
        "messages": messages,
    }
    if functions:
        payload["functions"] = functions
        payload["function_call"] = function_call
    if functions_state_id:
        payload["functions_state_id"] = functions_state_id

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
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiError("GigaChat returned an empty response") from exc

    state_id = data.get("functions_state_id") or message.get("functions_state_id")
    return message, state_id


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
    messages = _build_messages(prompt, system_instruction, history, summary)
    message = chat_completion(messages, client=client)
    answer = message.get("content")
    if not answer:
        raise GeminiError("GigaChat returned an empty response")
    return answer.strip()
