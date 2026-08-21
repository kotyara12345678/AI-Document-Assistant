"""Multi-stage document generation pipeline for large documents.

When a user requests a large document (e.g. a 30-page report, a comprehensive
contract, or a technical specification), the single-shot LLM response is
insufficient because GigaChat's output token limit (~2048 tokens) can only
produce ~1500 words. This pipeline bypasses that limitation:

    User request
    → Outline generation (small, fits in one response)
    → Section-by-section generation (each section fits in one response)
    → Assembly into a complete Markdown document
    → Optional consistency check
    → Parse into DocumentSpec → render → validate → save

Each section is generated independently with context:
- Document title and outline
- Brief descriptions of adjacent sections
- Key terms and entities to maintain consistency

The pipeline is triggered when:
1. The LLM explicitly passes pipeline=True in create_document, OR
2. The user request implies a large document and content is too short
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field

from app.core.config import settings
from app.services import gemini

logger = logging.getLogger("app.generation_pipeline")

# Maximum estimated words in content before pipeline is considered
_PIPELINE_WORD_THRESHOLD = 800


@dataclass
class SectionInfo:
    """One section of the document outline."""
    index: int
    heading: str
    level: int
    purpose: str
    key_terms: list[str] = field(default_factory=list)
    depends_on: list[int] = field(default_factory=list)


@dataclass
class DocumentOutline:
    """Structured outline for a large document."""
    title: str
    sections: list[SectionInfo]
    key_entities: list[str] = field(default_factory=list)
    style_notes: str = ""


def _llm_generate(
    prompt: str,
    system_instruction: str,
    max_tokens: int | None = None,
    temperature: float = 0.3,
) -> str:
    """Call the LLM with retry on transient errors."""
    max_tokens = max_tokens or settings.DOCUMENT_PIPELINE_MAX_TOKENS
    retries = settings.DOCUMENT_PIPELINE_SECTION_RETRIES
    for attempt in range(retries + 1):
        try:
            messages = gemini._build_messages(prompt, system_instruction)
            payload = {
                "model": settings.GIGACHAT_MODEL,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            client = gemini._get_shared_client()
            token = gemini._get_access_token(client)
            response = client.post(
                gemini._chat_url(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            message = data["choices"][0]["message"]
            content = message.get("content") or ""
            return content.strip()
        except Exception as exc:
            if attempt < retries:
                logger.warning(
                    "Pipeline LLM call failed (attempt %s/%s): %s; retrying",
                    attempt + 1, retries + 1, exc,
                )
                time.sleep(2.0 * (attempt + 1))
                continue
            raise


def _parse_outline(raw: str, title: str) -> DocumentOutline:
    """Parse the LLM's outline response into a DocumentOutline."""
    # Try to extract JSON from the response
    cleaned = raw.strip()
    # Remove markdown code fences if present
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find JSON object in the response
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                logger.warning("Failed to parse outline JSON; using fallback")
                return _fallback_outline(title, cleaned)
        else:
            return _fallback_outline(title, cleaned)

    sections = []
    for i, sec in enumerate(data.get("sections", [])):
        if not isinstance(sec, dict):
            continue
        heading = str(sec.get("heading", f"Раздел {i + 1}")).strip()
        level = int(sec.get("level", 1))
        purpose = str(sec.get("purpose", "")).strip()
        key_terms = [str(t) for t in sec.get("key_terms", []) if isinstance(t, str)]
        depends_on = [int(d) for d in sec.get("depends_on", []) if isinstance(d, (int, float))]
        sections.append(SectionInfo(
            index=i,
            heading=heading,
            level=level,
            purpose=purpose,
            key_terms=key_terms,
            depends_on=depends_on,
        ))

    if not sections:
        return _fallback_outline(title, raw)

    key_entities = [str(e) for e in data.get("key_entities", []) if isinstance(e, str)]
    style_notes = str(data.get("style_notes", "")).strip()

    return DocumentOutline(
        title=str(data.get("title", title)).strip() or title,
        sections=sections,
        key_entities=key_entities,
        style_notes=style_notes,
    )


def _fallback_outline(title: str, raw_text: str) -> DocumentOutline:
    """When JSON parsing fails, extract headings from the raw text."""
    sections = []
    lines = raw_text.split("\n")
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        # Try to detect heading-like lines
        level = 0
        if line.startswith("#"):
            level = min(len(line.split(" ")[0]), 3)
            heading = line.lstrip("#").strip()
        elif re.match(r"^\d+[\.\)]\s+", line):
            level = 2
            heading = re.sub(r"^\d+[\.\)]\s+", "", line).strip()
        else:
            continue
        if heading:
            sections.append(SectionInfo(
                index=len(sections),
                heading=heading,
                level=level or 1,
                purpose=f"Содержание раздела: {heading}",
            ))

    if not sections:
        # Absolute fallback: create a few generic sections
        sections = [
            SectionInfo(index=0, heading="Введение", level=1, purpose="Общее введение в тему"),
            SectionInfo(index=1, heading="Основная часть", level=1, purpose="Подробное описание"),
            SectionInfo(index=2, heading="Заключение", level=1, purpose="Итоги и выводы"),
        ]

    return DocumentOutline(title=title, sections=sections)


def _generate_outline(
    user_request: str,
    title: str,
    source_content: str | None = None,
) -> DocumentOutline:
    """Generate a document outline from the user request."""
    system = (
        "Ты — генератор планов документов. По запросу пользователя составь "
        "структурированный план (outline) документа.\n\n"
        "Верни ТОЛЬКО JSON объект со следующей структурой:\n"
        "{\n"
        '  "title": "Название документа",\n'
        '  "key_entities": ["Иванов И.И.", "ООО Ромашка", ...],\n'
        '  "style_notes": "Краткие заметки о стиле/формате",\n'
        '  "sections": [\n'
        "    {\n"
        '      "heading": "1. Название раздела",\n'
        '      "level": 1,\n'
        '      "purpose": "Описание что должно быть в этом разделе",\n'
        '      "key_terms": ["термин1", "термин2"],\n'
        '      "depends_on": []\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Правила:\n"
        "- sections от 5 до 25 разделов (для большого документа)\n"
        "- level: 1 для основных разделов, 2 для подразделов\n"
        "- purpose: чётко опиши что должно быть в каждом разделе\n"
        "- key_terms: ключевые термины которые нужно использовать\n"
        "- depends_on: индексы разделов от которых зависит этот\n"
        "- key_entities: имена, организации, суммы из запроса\n"
        "- Не добавляй markdown fences, только чистый JSON"
    )

    prompt = f"Запрос пользователя: {user_request}\n\nНазвание документа: {title}"
    if source_content:
        # Truncate source content for outline generation
        truncated = source_content[:8000]
        prompt += f"\n\nИсходные данные:\n{truncated}"

    raw = _llm_generate(prompt, system, max_tokens=2048, temperature=0.2)
    return _parse_outline(raw, title)


def _generate_section(
    outline: DocumentOutline,
    section: SectionInfo,
    source_content: str | None = None,
    previous_section_summaries: list[str] | None = None,
) -> str:
    """Generate the content for one section of the document."""
    # Build context about adjacent sections
    context_parts = []
    if section.depends_on:
        for dep_idx in section.depends_on:
            if 0 <= dep_idx < len(outline.sections) and dep_idx < section.index:
                context_parts.append(
                    f"Предыдущий раздел «{outline.sections[dep_idx].heading}»: "
                    f"{outline.sections[dep_idx].purpose}"
                )

    if previous_section_summaries:
        # Include the last 2 section summaries for continuity
        recent = previous_section_summaries[-2:]
        for i, summary in enumerate(recent):
            context_parts.append(f"Предыдущий раздел (кратко): {summary[:500]}")

    context_str = "\n".join(context_parts) if context_parts else "Начало документа"

    system = (
        "Ты — генератор содержания документов. Напиши содержание ОДНОГО раздела "
        "документа в формате Markdown.\n\n"
        "ПРАВИЛА:\n"
        "- Пиши как专业人士льный документ, НЕ как ответ в чате\n"
        "- Используй заголовки уровня 2+ (## или ###) для подразделов\n"
        "- Используй списки (- и 1.) где уместно\n"
        "- Используй таблицы (| col | col |) где уместно\n"
        "- Пиши развёрнуто: каждый раздел должен содержать существенный текст\n"
        "- НЕ пиши meta-комментарии вроде «далее можно добавить»\n"
        "- НЕ повторяй заголовок документа\n"
        "- Строго следуй purpose раздела\n"
        "- Используй указанные key_terms\n"
        "- Длина раздела: от 300 до 2000 слов\n"
        "- Верни ТОЛЬКО Markdown содержание раздела, без пояснений"
    )

    prompt = (
        f"Документ: «{outline.title}»\n"
        f"Раздел: {section.heading}\n"
        f"Уровень вложенности: {'H1' if section.level == 1 else 'H2' if section.level == 2 else 'H3'}\n\n"
        f"Назначение раздела:\n{section.purpose}\n\n"
        f"Ключевые термины: {', '.join(section.key_terms) if section.key_terms else 'не указаны'}\n\n"
        f"Контекст:\n{context_str}\n"
    )

    if source_content:
        truncated = source_content[:6000]
        prompt += f"\n\nИсходные данные для этого раздела:\n{truncated}\n"

    prompt += "\n\nНапиши содержание раздела в формате Markdown:"

    content = _llm_generate(
        prompt, system,
        max_tokens=settings.DOCUMENT_PIPELINE_MAX_TOKENS,
        temperature=0.3,
    )

    # Clean up: remove any meta-commentary the model might add
    content = _clean_section_content(content)
    return content


def _clean_section_content(content: str) -> str:
    """Remove meta-commentary and clean up section content."""
    # Remove common meta-phrases
    meta_patterns = [
        r"(?:^|\n)(?:Далее|В следующем разделе|В заключение|В конце|Примечание)[^.]*\.\s*",
        r"(?:^|\n)(?:Можно (?:также|добавить|включить)|Стоит (?:отметить|добавить))[^.]*\.\s*",
        r"(?:^|\n)(?:Этот раздел|Данный раздел|Настоящий раздел)\s+(?:описывает|содержит|представляет)[^.]*\.\s*",
    ]
    for pattern in meta_patterns:
        content = re.sub(pattern, "\n", content, flags=re.IGNORECASE)

    # Remove trailing incomplete sentences
    lines = content.rstrip().split("\n")
    if lines:
        last_line = lines[-1].rstrip()
        # If the last line ends mid-sentence (no punctuation), truncate
        if last_line and not last_line.endswith((".", "!", "?", ":", "»", ")", "-", "|")):
            if not last_line.startswith(("-", "*", "|", "#", "1.", "2.", "3.")):
                lines[-1] = last_line.rsplit(".", 1)[0] + "." if "." in last_line else last_line

    return "\n".join(lines).strip()


def _generate_section_with_retry(
    outline: DocumentOutline,
    section: SectionInfo,
    source_content: str | None = None,
    previous_section_summaries: list[str] | None = None,
) -> tuple[str, int]:
    """Generate a section with retry. Returns (content, retry_count)."""
    retries = settings.DOCUMENT_PIPELINE_SECTION_RETRIES
    last_error = None

    for attempt in range(retries + 1):
        try:
            content = _generate_section(
                outline, section, source_content, previous_section_summaries
            )
            # Basic validation: content should be non-empty and have some structure
            if len(content.strip()) > 50:
                return content, attempt
            last_error = f"Section content too short ({len(content.strip())} chars)"
            logger.warning(
                "Section %s generation produced short content (attempt %s/%s)",
                section.heading, attempt + 1, retries + 1,
            )
        except Exception as exc:
            last_error = str(exc)
            logger.warning(
                "Section %s generation failed (attempt %s/%s): %s",
                section.heading, attempt + 1, retries + 1, exc,
            )

        if attempt < retries:
            time.sleep(2.0 * (attempt + 1))

    # All retries exhausted — return what we have or a placeholder
    logger.error(
        "Section %s generation failed after %s retries: %s",
        section.heading, retries, last_error,
    )
    return f"**{section.heading}**\n\n{section.purpose}", retries


def _assemble_sections(
    outline: DocumentOutline,
    section_contents: list[str],
) -> str:
    """Assemble section contents into a complete Markdown document."""
    parts = [f"# {outline.title}", ""]

    for i, (section, content) in enumerate(zip(outline.sections, section_contents)):
        # Add section heading if not already present at the right level
        heading_prefix = "#" * min(section.level + 1, 6)  # +1 because title is H1
        if not content.strip().startswith("#"):
            parts.append(f"{heading_prefix} {section.heading}")
            parts.append("")
        parts.append(content)
        parts.append("")  # blank line between sections

    return "\n".join(parts).strip() + "\n"


def _consistency_check(
    document_content: str,
    outline: DocumentOutline,
) -> str:
    """Ask the LLM to review the assembled document for consistency."""
    system = (
        "Ты — редактор документов. Проверь assembled документ на:\n"
        "1. Противоречия (разные цифры/даты/имена в разных разделах)\n"
        "2. Повторы (одинаковые абзацы в разных разделах)\n"
        "3. Потерю информации (разделы упомянуты в плане но отсутствуют)\n"
        "4. Непоследовательность стиля\n\n"
        "Верни исправленный документ целиком (Markdown).\n"
        "Если всё ОК, верни документ без изменений.\n"
        "НЕ пиши комментарии — только текст документа."
    )

    # Truncate if too long for one LLM call
    if len(document_content) > 30000:
        document_content_for_check = document_content[:30000] + "\n\n[...остальная часть документа...]"

    prompt = (
        f"Документ «{outline.title}»:\n\n"
        f"{document_content_for_check}\n\n"
        "Проверь и исправь (если нужно). Верни полный исправленный документ:"
    )

    try:
        result = _llm_generate(prompt, system, max_tokens=4096, temperature=0.1)
        # If the result is significantly shorter than the original, something went wrong
        if len(result) < len(document_content) * 0.5:
            logger.warning("Consistency check truncated document; keeping original")
            return document_content
        return result
    except Exception as exc:
        logger.warning("Consistency check failed: %s; keeping original", exc)
        return document_content


def generate_large_document(
    user_request: str,
    title: str,
    source_content: str | None = None,
    *,
    outline: DocumentOutline | None = None,
) -> str:
    """Generate a large document using the multi-stage pipeline.

    Returns the complete Markdown content of the document.
    """
    total_retries = 0
    max_total_retries = settings.DOCUMENT_PIPELINE_MAX_TOTAL_RETRIES

    # Stage 1: Generate outline (if not provided)
    if outline is None:
        logger.info("Pipeline: generating outline for '%s'", title)
        outline = _generate_outline(user_request, title, source_content)
        logger.info(
            "Pipeline: outline generated with %d sections",
            len(outline.sections),
        )

    # Stage 2: Generate each section
    section_contents: list[str] = []
    section_summaries: list[str] = []

    for i, section in enumerate(outline.sections):
        if total_retries >= max_total_retries:
            logger.warning(
                "Pipeline: max total retries (%s) reached at section %d/%d",
                max_total_retries, i + 1, len(outline.sections),
            )
            # Fill remaining sections with placeholders
            remaining = len(outline.sections) - len(section_contents)
            for j in range(remaining):
                sec = outline.sections[len(section_contents) + j]
                section_contents.append(f"## {sec.heading}\n\n{sec.purpose}")
            break

        logger.info(
            "Pipeline: generating section %d/%d: %s",
            i + 1, len(outline.sections), section.heading,
        )

        content, retries = _generate_section_with_retry(
            outline, section, source_content, section_summaries
        )
        total_retries += retries
        section_contents.append(content)

        # Create a brief summary for context passing
        summary = content[:300].replace("\n", " ").strip()
        section_summaries.append(summary)

    # Stage 3: Assemble
    logger.info("Pipeline: assembling %d sections", len(section_contents))
    document = _assemble_sections(outline, section_contents)

    # Stage 4: Consistency check (optional)
    if settings.DOCUMENT_PIPELINE_CONSISTENCY_CHECK:
        logger.info("Pipeline: running consistency check")
        document = _consistency_check(document, outline)

    logger.info(
        "Pipeline: document generated (%d words, %d sections)",
        len(document.split()),
        len(outline.sections),
    )

    return document


def should_use_pipeline(content: str | None, user_request: str) -> bool:
    """Determine if the multi-stage pipeline should be used.

    Returns True when:
    1. Content is None/empty (no content provided, LLM might struggle)
    2. Content is too short relative to the request complexity
    3. The request explicitly asks for a large/long document
    """
    if not settings.DOCUMENT_PIPELINE_ENABLED:
        return False

    # Explicit signals in the user request
    request_lower = (user_request or "").lower()
    large_doc_signals = (
        "подробный", "большой", "длинный", "объёмный", "комплексный",
        "детальный", "полный", "развёрнутый", "многостранич",
        "на 20 страниц", "на 30 страниц", "на 50 страниц",
        "техническ", "руководств", "инструкци",
        "отчёт", "договор", "контракт",
    )
    if any(signal in request_lower for signal in large_doc_signals):
        return True

    # Content is too short for the request
    if content:
        word_count = len(content.split())
        if word_count < _PIPELINE_WORD_THRESHOLD:
            return True

    return False
