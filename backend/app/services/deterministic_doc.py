"""Deterministic document tasks.

Some document requests are fully determined by the wording and need no
creativity from the LLM:

* "создай docx где 50 раз будет повторяться \"витек лох\""
* "создай пустой docx"
* "сохрани текст: <...>"

For these we build the ``DocumentSpec`` in plain Python (so the repetition
count is exact and the LLM never has to hand-write 50 lines) and hand it to the
normal render/save pipeline. This keeps the architecture intact:

    user request -> agent -> structured DocumentSpec
                 -> docx_renderer / odt_renderer -> .docx/.odt
                 -> save/register -> download URL

and it works even when GigaChat is unavailable.

Detection is deliberately conservative: only unambiguous phrasings trigger it, so
real template-based generation keeps going through the LLM.
"""

import re
from dataclasses import dataclass

from app.core.config import settings
from app.schemas.document_spec import DocumentSpec, ParagraphBlock


@dataclass
class DeterministicDocTask:
    output_format: str
    title: str
    spec: DocumentSpec


def _extract_quoted(text: str) -> str | None:
    for pattern in (r'"([^"]+)"', r"\u00ab([^\u00bb]+)\u00bb", r"«([^»]+)»", r"'([^']+)'"):
        m = re.search(pattern, text)
        if m:
            value = m.group(1).strip()
            if value:
                return value
    return None


def _detect_format(text: str) -> str:
    low = text.lower()
    if re.search(r"\bmd\b", low) or "markdown" in low:
        return "md"
    if re.search(r"\btxt\b", low):
        return "txt"
    if "odt" in low:
        return "odt"
    if "pdf" in low:
        return "pdf"
    if "docx" in low:
        return "docx"
    return "docx"


def _detect_title(text: str) -> str | None:
    m = re.search(r'(?:docx|odt|pdf|md|txt|документ)\s*["«]([^"»]+)["»]', text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def _detect_repeat(text: str) -> tuple[str, int] | None:
    number = re.search(r"(\d+)\s*раз", text, re.IGNORECASE)
    has_repeat_word = bool(re.search(r"повторя", text, re.IGNORECASE))
    quoted = _extract_quoted(text)
    if number and has_repeat_word and quoted:
        count = int(number.group(1))
        count = max(1, min(count, settings.AGENT_DOCUMENT_MAX_PARAGRAPHS))
        return quoted, count
    return None


def _detect_empty(text: str) -> bool:
    return bool(
        re.search(r"пуст\w*\s+(?:документ|docx|odt|pdf|md|txt)", text, re.IGNORECASE)
    )


def _detect_provided_text(text: str) -> str | None:
    for pattern in (
        r"сохрани(?:те)?\s+текст[:\s]+(.*)",
        r"запиши(?:те)?\s+текст[:\s]+(.*)",
        r"создай(?:те)?\s+документ\s+из\s+текста[:\s]+(.*)",
        r"создай(?:те)?\s+документ\s+с\s+текстом[:\s]+(.*)",
    ):
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if m:
            body = m.group(1).strip()
            if body:
                return body
    return None


def detect_deterministic_document_task(question: str) -> DeterministicDocTask | None:
    """Return a deterministic task, or ``None`` if the LLM should handle it."""
    if not question or not question.strip():
        return None

    q = question.strip()

    repeat = _detect_repeat(q)
    if repeat is not None:
        repeated_text, count = repeat
        blocks = [ParagraphBlock(text=repeated_text) for _ in range(count)]
        title = _detect_title(q) or settings.AGENT_DOCUMENT_DEFAULT_TITLE
        return DeterministicDocTask(
            output_format=_detect_format(q),
            title=title,
            spec=DocumentSpec(title=title, blocks=blocks),
        )

    if _detect_empty(q):
        title = _detect_title(q) or settings.AGENT_DOCUMENT_DEFAULT_TITLE
        # A single non-empty paragraph keeps the spec valid while rendering a
        # near-empty page (the title is also emitted as a heading).
        return DeterministicDocTask(
            output_format=_detect_format(q),
            title=title,
            spec=DocumentSpec(title=title, blocks=[ParagraphBlock(text=" ")]),
        )

    provided = _detect_provided_text(q)
    if provided is not None:
        title = _detect_title(q) or settings.AGENT_DOCUMENT_DEFAULT_TITLE
        blocks = [
            ParagraphBlock(text=line.strip())
            for line in provided.splitlines()
            if line.strip()
        ]
        if not blocks:
            blocks = [ParagraphBlock(text=provided)]
        return DeterministicDocTask(
            output_format=_detect_format(q),
            title=title,
            spec=DocumentSpec(title=title, blocks=blocks),
        )

    return None


def build_spec_from_task(task: DeterministicDocTask) -> DocumentSpec:
    """Return the validated spec for a deterministic task (re-validates)."""
    return DocumentSpec.model_validate(task.spec.model_dump())
