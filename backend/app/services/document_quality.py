"""Document output quality gate.

Detects unfilled placeholders in generated documents and decides whether the
document is "ready" or still has critical gaps. Used as a validation step
right after document generation and before the agent's final answer, so a
document with unfilled critical fields is never presented as fully finished.

Design notes
------------
* Anything between ``{{ ... }}`` is considered a template marker and — unless
  the user explicitly asked for a template — is treated as a critical gap.
* ``[text]`` square brackets are NOT automatically an error: legal quotes and
  citation brackets (``[1]``, ``[Приложение А]``) are fine. Only a small set of
  known placeholder words inside brackets is flagged.
* Explicit "not provided" markers (``[НЕ УКАЗАНО]``, ``[не указано]``, TODO,
  TBD, ``[дата]``, ``[подписи]`` …) are always flagged because they mean the
  model admitted it did not complete the field.
* Templates requested by the user (``по шаблону``, ``по образцу``, ``готовый
  шаблон``) legitimately keep their placeholder slots — they are not flagged.
"""

import re

# Known placeholder words inside [...] that mean "this field was not filled".
_SQUARE_BRACKET_PLACEHOLDER_WORDS = (
    # Russian field names
    "дата",
    "данные",
    "имя",
    "фамилия",
    "адрес",
    "срок",
    "сумма",
    "подпись",
    "подписи",
    "реквизиты",
    "номер",
    "паспорт",
    "телефон",
    "фио",
    "счет",
    "город",
    "место",
    "должность",
    "печать",
    "полное наименование",
    "инн",
    "кпп",
    "не указано",
    "впишите",
    "заполните",
    "укажите",
    "tbd",
    "todo",
    "n/a",
)

# English field names (mixed-language documents).
_SQUARE_BRACKET_PLACEHOLDER_WORDS_EN = (
    "date",
    "name",
    "address",
    "term",
    "amount",
    "signature",
    "signatures",
    "phone",
    "number",
    "passport",
    "place",
    "position",
    "details",
    "not specified",
    "unknown",
)

_DOUBLE_BRACE_RE = re.compile(r"\{\{.*?\}\}", re.IGNORECASE | re.DOTALL)
_SQUARE_RE = re.compile(r"\[([^\]]{0,60})\]", re.IGNORECASE | re.DOTALL)
_HARMLESS_SQUARE_RE = re.compile(r"^\[\d+\]$", re.IGNORECASE | re.DOTALL)

_TEMPLATE_HINTS = (
    "по шаблону",
    "по образцу",
    "по примеру",
    "готовый шаблон",
    "шаблон",
    "образец договора",
    "типовой",
    "бланк",
)


def is_template_request(question: str) -> bool:
    """True when the user explicitly asked for a template/example document.

    Template requests legitimately keep placeholder slots, so we must not warn
    about them. A request for a *ready* document has the opposite intent.
    """
    text = (question or "").lower()
    return any(hint in text for hint in _TEMPLATE_HINTS)


def _square_bracket_flag(content: str) -> bool:
    inner = content.strip().lower()
    if not inner:
        return False
    for word in _SQUARE_BRACKET_PLACEHOLDER_WORDS + _SQUARE_BRACKET_PLACEHOLDER_WORDS_EN:
        # Match whole bracketed phrase or "word ... " prefix so '[дата]',
        # '[дата подписания]' and 'SUBJECT: [дата ...]' all match, but a
        # normal bracket like '[Приложение А]' does not.
        if inner == word or inner.startswith(word + " ") or inner.startswith(word + "."):
            return True
    return False


def find_placeholders(content: str) -> list[str]:
    """Return the placeholder fragments found in a document's text.

    Returns an ordered, de-duplicated list of the *actual* unfilled markers
    (e.g. ``{{SALARY}}``, ``[дата]``, ``[НЕ УКАЗАНО]``, ``TODO``). Empty list
    means the document has no critical gaps and may be presented as ready.
    """
    text = content or ""
    hits: list[str] = []

    # {{ ... }} markers are always critical.
    hits.extend(m.group(0) for m in _DOUBLE_BRACE_RE.finditer(text))

    # [ ... ] with known placeholder words (never plain citation brackets).
    for m in _SQUARE_RE.finditer(text):
        fragment = m.group(0)
        if _HARMLESS_SQUARE_RE.match(fragment):
            continue
        if _square_bracket_flag(fragment[1:-1]):
            hits.append(fragment)

    # Bare TODO / TBD / N/A tokens.
    for word in ("TODO", "TBD", "N/A"):
        hits.extend(re.findall(rf"\b{re.escape(word)}\b", text, flags=re.IGNORECASE))

    seen: set[str] = set()
    unique: list[str] = []
    for hit in hits:
        key = hit.strip().lower()
        if key not in seen:
            seen.add(key)
            unique.append(hit.strip())
    return unique


def format_placeholder_warning(placeholders: list[str]) -> str:
    """Human-readable warning line for the final answer."""
    if not placeholders:
        return ""
    listed = ", ".join(placeholders)
    return (
        "Документ создан, но в нём остались незаполненные поля: "
        f"{listed}. Уточните эти данные, и я дополню документ — либо укажите, "
        "что так и должно быть (например, «это шаблон»)."
    )