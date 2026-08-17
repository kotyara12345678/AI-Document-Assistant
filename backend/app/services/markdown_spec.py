"""Convert a Markdown document body into a validated ``DocumentSpec``.

GigaChat (and many function-calling models) reliably fill a single large
``content`` string argument but struggle with deeply nested object/array
schemas such as the structured ``document_spec.blocks``. To keep document
generation robust we let the model emit the whole document body as Markdown
and parse it here into the same safe ``DocumentSpec`` the renderers consume.
This way the renderers and the rest of the pipeline are unchanged, and the
model only has to produce prose with a handful of well-known markers.

Supported Markdown:

* Headings: ``#`` .. ``######`` (level = number of ``#``).
* Paragraphs: free text, blank-line separated.
* Lists: ``- item`` / ``* item`` / ``+ item`` (bulleted) and ``1. item``
  (numbered). Consecutive items of the same kind are grouped.
* Tables: GitHub-flavored pipe tables with a ``|---|---|`` separator row.

Inline emphasis markers (``**bold**``, ``*italic*``, ``_italic_``,
```code` ``) are stripped so the renderers receive plain text, since the
spec/renderers do not model inline formatting.
"""

from app.core.config import settings
from app.schemas.document_spec import (
    DocumentMetadata,
    DocumentSpec,
    HeadingBlock,
    ListBlock,
    ParagraphBlock,
    TableBlock,
)

_LIST_BULLET = ("-", "*", "+")
_TABLE_SEPARATOR = "---"


def _unescape(text: str) -> str:
    """Normalize literal backslash escapes some models emit instead of real
    newlines/tabs (e.g. ``\\n`` where a real newline was meant)."""
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
    text = text.replace("\\r", "\n").replace("\\t", "\t")
    return text


def _strip_inline(text: str) -> str:
    """Remove inline markdown emphasis markers, keeping the inner text."""
    out = text
    out = out.replace("**", "").replace("__", "")
    out = out.replace("*", "").replace("_", "")
    out = out.replace("`", "")
    return out.strip()


def _is_table_separator(line: str) -> bool:
    stripped = line.strip().strip("|").strip()
    if not stripped:
        return False
    cells = [c.strip() for c in stripped.split("|")]
    if len(cells) < 2:
        return False
    return all(set(c) <= set("-: ") and c for c in cells)


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_heading(line: str):
    if not line.startswith("#"):
        return None
    level = 0
    for ch in line:
        if ch == "#":
            level += 1
        else:
            break
    if level > 6:
        return None
    rest = line[level:].strip()
    if not rest:
        return None
    return level, rest


def _is_list_item(line: str):
    stripped = line.lstrip()
    if stripped[:2] in (f"{c} " for c in _LIST_BULLET):
        return False, stripped[2:].strip()
    import re

    match = re.match(r"^\s*\d+\.\s+(.*)$", stripped)
    if match:
        return True, match.group(1).strip()
    return None


def markdown_to_spec(
    markdown: str,
    title: str | None = None,
    author: str | None = None,
    subject: str | None = None,
    keywords: str | None = None,
) -> DocumentSpec:
    """Parse ``markdown`` into a ``DocumentSpec``.

    ``title`` overrides any heading-derived title. The first level-1 heading is
    used as the title when no explicit title is supplied; otherwise a sensible
    default is used. At least one content block is always produced.
    """
    lines = _unescape(markdown or "").replace("\r\n", "\n").split("\n")

    blocks: list = []
    title_from_md: str | None = None
    paragraph_buffer: list[str] = []

    def flush_paragraph() -> None:
        if paragraph_buffer:
            text = " ".join(p.strip() for p in paragraph_buffer if p.strip())
            text = _strip_inline(text)
            if text:
                blocks.append(ParagraphBlock(text=text))
            paragraph_buffer.clear()

    idx = 0
    n = len(lines)
    while idx < n:
        raw = lines[idx]
        line = raw.rstrip()

        # Blank line -> paragraph boundary.
        if not line.strip():
            flush_paragraph()
            idx += 1
            continue

        # Heading.
        heading = _is_heading(line)
        if heading is not None:
            flush_paragraph()
            level, text = heading
            text = _strip_inline(text)
            if level == 1 and title_from_md is None:
                title_from_md = text
            blocks.append(HeadingBlock(level=min(level, 6), text=text))
            idx += 1
            continue

        # Table (header line followed by a separator line).
        if "|" in line and idx + 1 < n and _is_table_separator(lines[idx + 1]):
            flush_paragraph()
            headers = _split_row(line)
            headers = [_strip_inline(h) for h in headers]
            idx += 2
            rows: list[list[str]] = []
            while idx < n and "|" in lines[idx] and lines[idx].strip():
                cells = _split_row(lines[idx])
                cells = [_strip_inline(c) for c in cells]
                # Pad/trim to header width for stable rendering.
                if len(cells) < len(headers):
                    cells += [""] * (len(headers) - len(cells))
                elif len(cells) > len(headers):
                    cells = cells[: len(headers)]
                rows.append(cells)
                idx += 1
            blocks.append(TableBlock(headers=headers, rows=rows))
            continue

        # List.
        list_info = _is_list_item(line)
        if list_info is not None:
            flush_paragraph()
            ordered, first_text = list_info
            items: list[str] = [_strip_inline(first_text)]
            idx += 1
            while idx < n:
                nxt = lines[idx].rstrip()
                if not nxt.strip():
                    # Allow a single blank line inside a list only if the next
                    # non-blank line is also a list item; otherwise end list.
                    j = idx + 1
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n and _is_list_item(lines[j]) is not None:
                        idx = j
                        continue
                    break
                info = _is_list_item(nxt)
                if info is None or info[0] != ordered:
                    break
                items.append(_strip_inline(info[1]))
                idx += 1
            blocks.append(ListBlock(ordered=ordered, items=items))
            continue

        # Default: each non-blank line becomes its own paragraph. This matches
        # how the model emits line-oriented document content (one clause / one
        # key-value pair per line) rather than strict Markdown soft-wrap rules.
        text = _strip_inline(line)
        if text:
            blocks.append(ParagraphBlock(text=text))
        idx += 1

    flush_paragraph()

    resolved_title = (title or title_from_md or "").strip() or settings.AGENT_DOCUMENT_DEFAULT_TITLE
    metadata = DocumentMetadata(
        author=author or None,
        subject=subject or None,
        keywords=keywords or None,
    )

    if not blocks:
        blocks.append(ParagraphBlock(text=resolved_title))

    return DocumentSpec(title=resolved_title, metadata=metadata, blocks=blocks)
