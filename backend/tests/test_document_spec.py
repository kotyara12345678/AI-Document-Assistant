"""Validation of the DocumentSpec (the safe model the LLM must produce).

All limit checks must reject oversized input with a Pydantic ValidationError —
the create_document tool turns those into safe structured errors.
"""

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.document_spec import (
    DocumentSpec,
    HeadingBlock,
    ListBlock,
    ParagraphBlock,
    TableBlock,
)


def _valid_spec() -> dict:
    return {
        "title": "Трудовой договор",
        "author": "Иванов Иван",
        "blocks": [
            {"type": "heading", "level": 1, "text": "1. Общие положения"},
            {"type": "paragraph", "text": "Стороны заключили договор."},
            {"type": "heading", "level": 1, "text": "2. Стороны"},
            {"type": "list", "ordered": True, "items": ["Работник", "Работодатель"]},
        ],
    }


def test_valid_spec_passes():
    spec = DocumentSpec.model_validate(_valid_spec())
    assert spec.title == "Трудовой договор"
    assert [type(b) for b in spec.blocks] == [
        HeadingBlock,
        ParagraphBlock,
        HeadingBlock,
        ListBlock,
    ]
    assert spec.blocks[1].type == "paragraph"
    assert spec.blocks[3].type == "list"
    assert spec.blocks[3].ordered is True


def test_valid_spec_with_table_passes():
    spec = DocumentSpec.model_validate(
        {
            "title": "Отчёт",
            "blocks": [
                {"type": "heading", "level": 2, "text": "Данные"},
                {"type": "table", "headers": ["Показатель", "Значение"],
                 "rows": [["Квота", "150000"], ["Срок", "1 год"]]},
            ],
        }
    )
    assert spec.blocks[1].type == "table"
    assert spec.blocks[1].rows == [["Квота", "150000"], ["Срок", "1 год"]]


def test_legacy_sections_shape_is_normalized():
    spec = DocumentSpec.model_validate(
        {
            "title": "Трудовой договор",
            "author": "Иванов Иван",
            "sections": [
                {
                    "heading": "1. Общие положения",
                    "blocks": [
                        {"type": "paragraph", "text": "Стороны заключили договор."},
                    ],
                },
                {
                    "heading": "2. Стороны",
                    "blocks": [
                        {
                            "type": "table",
                            "columns": ["Показатель", "Значение"],
                            "rows": [["Квота", "150000"]],
                        },
                    ],
                },
            ],
        }
    )
    assert [type(b) for b in spec.blocks] == [
        HeadingBlock,
        ParagraphBlock,
        HeadingBlock,
        TableBlock,
    ]
    assert spec.blocks[0].level == 1
    assert spec.blocks[2].text == "2. Стороны"
    assert spec.blocks[3].headers == ["Показатель", "Значение"]
    assert spec.metadata.author == "Иванов Иван"


def test_missing_title_rejected():
    with pytest.raises(ValidationError):
        DocumentSpec.model_validate({"blocks": []})


def test_title_too_long_rejected():
    with pytest.raises(ValidationError):
        DocumentSpec.model_validate(
            {"title": "x" * (settings.AGENT_DOCUMENT_MAX_LINE_CHARS + 1)}
        )


def test_too_long_paragraph_rejected():
    with pytest.raises(ValidationError):
        DocumentSpec.model_validate(
            {
                "title": "Док",
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": "x" * (settings.AGENT_DOCUMENT_MAX_LINE_CHARS + 1),
                    }
                ],
            }
        )


def test_too_many_headings_rejected():
    spec = _valid_spec()
    spec["blocks"] = [
        {"type": "heading", "level": 1, "text": f"Заголовок {i}"}
        for i in range(settings.AGENT_DOCUMENT_MAX_SECTIONS + 1)
    ]
    with pytest.raises(ValidationError):
        DocumentSpec.model_validate(spec)


def test_too_many_paragraphs_rejected():
    blocks = [
        {"type": "paragraph", "text": f"абзац {i}"}
        for i in range(settings.AGENT_DOCUMENT_MAX_PARAGRAPHS + 1)
    ]
    spec = {"title": "Док", "blocks": blocks}
    with pytest.raises(ValidationError):
        DocumentSpec.model_validate(spec)


def test_too_many_table_rows_rejected():
    rows = [
        ["x"] * 2 for _ in range(settings.AGENT_DOCUMENT_MAX_TABLE_ROWS + 1)
    ]
    spec = {
        "title": "Док",
        "blocks": [{"type": "table", "headers": ["a", "b"], "rows": rows}],
    }
    with pytest.raises(ValidationError):
        DocumentSpec.model_validate(spec)


def test_document_total_chars_capped():
    line = "x" * settings.AGENT_DOCUMENT_MAX_LINE_CHARS
    blocks = [
        {"type": "paragraph", "text": line}
        for _ in range(
            (settings.AGENT_DOCUMENT_MAX_CHARS // settings.AGENT_DOCUMENT_MAX_LINE_CHARS) + 2
        )
    ]
    spec = {"title": "Док", "blocks": blocks}
    with pytest.raises(ValidationError):
        DocumentSpec.model_validate(spec)


def test_unknown_block_type_rejected():
    with pytest.raises(ValidationError):
        DocumentSpec.model_validate(
            {
                "title": "Док",
                "blocks": [{"type": "script", "code": "evil()"}],
            }
        )


def test_heading_level_range_enforced():
    with pytest.raises(ValidationError):
        DocumentSpec.model_validate(
            {"title": "Док", "blocks": [{"type": "heading", "level": 9, "text": "Заголовок"}]}
        )
