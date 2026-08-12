"""Safe, format-neutral model of a generated document.

The LLM never produces DOCX/ODT directly. It emits a structured
``DocumentSpec`` — a title, optional metadata and an ordered list of strictly
typed blocks (heading / paragraph / list / table) — which is validated here
before any file is rendered. The same spec feeds both the DOCX and the ODT
renderers, so there is no per-format content model.

Template orientation: the LLM is expected to preserve the structure of a
template (its headings, sections, paragraphs, lists, tables) as blocks and
substitute the values read from data documents into the matching places.

Safety: every text field is length-bounded and the whole document is capped
(blocks, list items, table rows, total characters) using the
``AGENT_DOCUMENT_MAX_*`` settings. Unknown block types and unexpected extra
fields (including any executable-looking payload) are rejected. Violations
raise Pydantic ValidationError, which the create_document tool converts into
a safe structured error — never a traceback.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import settings


def _check_line(value: str) -> str:
    limit = settings.AGENT_DOCUMENT_MAX_LINE_CHARS
    if len(value) > limit:
        raise ValueError(f"text is too long: {len(value)} chars (max {limit})")
    return value


class HeadingBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["heading"] = "heading"
    level: int = Field(default=1, ge=1, le=6)
    text: str = Field(min_length=1)

    _text = field_validator("text")(_check_line)


class ParagraphBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["paragraph"] = "paragraph"
    text: str = Field(min_length=1)

    _text = field_validator("text")(_check_line)


class ListBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["list"] = "list"
    ordered: bool = False
    items: list[str] = Field(default_factory=list)

    @field_validator("items")
    @classmethod
    def _check_items(cls, items: list[str]) -> list[str]:
        for item in items:
            _check_line(item)
        return items


class TableBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["table"] = "table"
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)

    @field_validator("headers")
    @classmethod
    def _check_headers(cls, headers: list[str]) -> list[str]:
        for header in headers:
            _check_line(header)
        return headers

    @field_validator("rows")
    @classmethod
    def _check_rows(cls, rows: list[list[str]]) -> list[list[str]]:
        for row in rows:
            for cell in row:
                _check_line(cell)
        return rows


Block = Annotated[
    HeadingBlock | ParagraphBlock | ListBlock | TableBlock,
    Field(discriminator="type"),
]


class DocumentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    author: str | None = Field(default=None)
    subject: str | None = Field(default=None)
    keywords: str | None = Field(default=None)

    @field_validator("author", "subject", "keywords")
    @classmethod
    def _check_meta(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _check_line(value)


class DocumentSpec(BaseModel):
    """A complete document: title, optional metadata and ordered blocks."""

    model_config = ConfigDict(extra="forbid")

    title: str
    metadata: DocumentMetadata = Field(default_factory=DocumentMetadata)
    blocks: list[Block] = Field(default_factory=list, min_length=1)

    @field_validator("title")
    @classmethod
    def _check_title(cls, value: str) -> str:
        return _check_line(value)

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_shape(cls, data):
        """Compatibility bridge: accept the legacy ``sections`` shape.

        Older prompts emitted ``{'sections': [{'heading': ..., 'blocks': [
        ...]}]}`` with flat metadata fields. Normalize that into the current
        ``blocks`` form so the backend keeps working with any cached/prompted
        legacy spec while the model-facing contract is the flat blocks shape.
        Top-level ``author``/``subject``/``keywords`` (also advertised by the
        create_document tool schema) are always relocated into ``metadata``.
        """
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        if "blocks" not in normalized and "sections" in normalized:
            blocks: list = []
            sections = data.get("sections") or []
            if isinstance(sections, list):
                for section in sections:
                    if not isinstance(section, dict):
                        continue
                    heading = section.get("heading")
                    if heading:
                        blocks.append({"type": "heading", "level": 1, "text": heading})
                    for block in section.get("blocks") or []:
                        if not isinstance(block, dict):
                            continue
                        # Legacy table blocks named the header row ``columns``.
                        if (
                            block.get("type") == "table"
                            and "columns" in block
                            and "headers" not in block
                        ):
                            headers = block.get("columns") or []
                            block = {
                                key: value
                                for key, value in block.items()
                                if key != "columns"
                            }
                            block["headers"] = headers
                        blocks.append(block)

            normalized = {
                key: value for key, value in data.items() if key != "sections"
            }
            normalized["blocks"] = blocks

        # Legacy flat metadata (author/subject/keywords) moves into ``metadata``.
        metadata = dict(normalized.get("metadata") or {})
        for key in ("author", "subject", "keywords"):
            value = normalized.get(key)
            if value is not None:
                metadata[key] = value
            normalized.pop(key, None)
        if metadata:
            normalized["metadata"] = metadata

        return normalized

    @model_validator(mode="after")
    def _check_aggregate_limits(self) -> "DocumentSpec":
        headings = 0
        blocks = len(self.blocks)
        list_items = 0
        table_rows = 0
        total_chars = len(self.title)
        for block in self.blocks:
            if isinstance(block, HeadingBlock):
                headings += 1
                total_chars += len(block.text)
            elif isinstance(block, ParagraphBlock):
                total_chars += len(block.text)
            elif isinstance(block, ListBlock):
                list_items += len(block.items)
                total_chars += sum(len(item) for item in block.items)
            elif isinstance(block, TableBlock):
                table_rows += len(block.rows)
                total_chars += sum(len(header) for header in block.headers)
                total_chars += sum(
                    len(cell) for row in block.rows for cell in row
                )

        if headings > settings.AGENT_DOCUMENT_MAX_SECTIONS:
            raise ValueError(
                f"too many headings: {headings} (max {settings.AGENT_DOCUMENT_MAX_SECTIONS})"
            )
        if blocks > settings.AGENT_DOCUMENT_MAX_PARAGRAPHS:
            raise ValueError(
                f"too many content blocks: {blocks} (max {settings.AGENT_DOCUMENT_MAX_PARAGRAPHS})"
            )
        if list_items > settings.AGENT_DOCUMENT_MAX_LIST_ITEMS:
            raise ValueError(
                f"too many list items: {list_items} (max {settings.AGENT_DOCUMENT_MAX_LIST_ITEMS})"
            )
        if table_rows > settings.AGENT_DOCUMENT_MAX_TABLE_ROWS:
            raise ValueError(
                f"too many table rows: {table_rows} (max {settings.AGENT_DOCUMENT_MAX_TABLE_ROWS})"
            )
        if total_chars > settings.AGENT_DOCUMENT_MAX_CHARS:
            raise ValueError(
                f"document is too large: {total_chars} chars (max {settings.AGENT_DOCUMENT_MAX_CHARS})"
            )
        return self
