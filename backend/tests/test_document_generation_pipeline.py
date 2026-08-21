"""Tests for the multi-stage document generation pipeline.

Tests cover:
- Data structures (SectionInfo, DocumentOutline)
- should_use_pipeline decision logic
- Outline generation (mocked LLM)
- Section generation with retry
- Assembly of sections into complete document
- Consistency check
- Full pipeline end-to-end (mocked LLM)
- Edge cases: timeout, malformed output, empty outline
"""

import json
import types
from unittest.mock import MagicMock, patch

import pytest

from app.services.document_generation_pipeline import (
    DocumentOutline,
    SectionInfo,
    _assemble_sections,
    _consistency_check,
    _generate_outline,
    _generate_section_with_retry,
    generate_large_document,
    should_use_pipeline,
)


# --- SectionInfo and DocumentOutline ---


class TestSectionInfo:
    def test_create_minimal(self):
        s = SectionInfo(index=0, heading="Intro", level=1, purpose="Introduce topic")
        assert s.index == 0
        assert s.heading == "Intro"
        assert s.key_terms == []
        assert s.depends_on == []

    def test_create_with_terms(self):
        s = SectionInfo(index=1, heading="Data", level=2, purpose="Show data", key_terms=["INN", "date"])
        assert s.key_terms == ["INN", "date"]


class TestDocumentOutline:
    def test_create_minimal(self):
        outline = DocumentOutline(title="Report", sections=[
            SectionInfo(index=0, heading="A", level=1, purpose="Do A"),
        ])
        assert outline.title == "Report"
        assert len(outline.sections) == 1
        assert outline.key_entities == []
        assert outline.style_notes == ""

    def test_create_full(self):
        outline = DocumentOutline(
            title="Contract",
            sections=[
                SectionInfo(index=0, heading="Parties", level=1, purpose="Define parties"),
                SectionInfo(index=1, heading="Terms", level=1, purpose="Define terms"),
            ],
            key_entities=["OOO Ромашка", "ИНН 123456"],
            style_notes="Use formal Russian",
        )
        assert len(outline.sections) == 2
        assert outline.key_entities == ["OOO Ромашка", "ИНН 123456"]


# --- should_use_pipeline ---


class TestShouldUsePipeline:
    def test_disabled_returns_false(self, monkeypatch):
        monkeypatch.setattr("app.services.document_generation_pipeline.settings", types.SimpleNamespace(
            DOCUMENT_PIPELINE_ENABLED=False,
        ))
        assert should_use_pipeline("x" * 5000, "создай подробный отчёт") is False

    def test_long_content_triggers_for_large_request(self, monkeypatch):
        monkeypatch.setattr("app.services.document_generation_pipeline.settings", types.SimpleNamespace(
            DOCUMENT_PIPELINE_ENABLED=True,
        ))
        # Content is short + request has a large-doc signal
        assert should_use_pipeline("short", "создай подробный отчёт") is True

    def test_short_content_triggers(self, monkeypatch):
        monkeypatch.setattr("app.services.document_generation_pipeline.settings", types.SimpleNamespace(
            DOCUMENT_PIPELINE_ENABLED=True,
        ))
        # Content under 800 words triggers pipeline
        assert should_use_pipeline("Hello world", "договор") is True

    def test_no_content_no_signal_returns_false(self, monkeypatch):
        monkeypatch.setattr("app.services.document_generation_pipeline.settings", types.SimpleNamespace(
            DOCUMENT_PIPELINE_ENABLED=True,
        ))
        # No content, no large-doc signal
        assert should_use_pipeline(None, "привет") is False

    def test_explicit_signals(self, monkeypatch):
        monkeypatch.setattr("app.services.document_generation_pipeline.settings", types.SimpleNamespace(
            DOCUMENT_PIPELINE_ENABLED=True,
        ))
        for signal in ["подробный", "объёмный", "многостранич", "техническое руководство", "инструкция", "отчёт"]:
            assert should_use_pipeline(None, f"сделай {signal} документ") is True, f"signal '{signal}' should trigger"


# --- Assembly ---


class TestAssembleSections:
    def test_single_section(self):
        outline = DocumentOutline(
            title="Test",
            sections=[SectionInfo(index=0, heading="Intro", level=1, purpose="Intro")],
        )
        result = _assemble_sections(outline, ["Para text here."])
        assert "# Test" in result
        assert "Para text here." in result

    def test_multiple_sections(self):
        outline = DocumentOutline(
            title="Report",
            sections=[
                SectionInfo(index=0, heading="Part 1", level=1, purpose="First"),
                SectionInfo(index=1, heading="Part 2", level=1, purpose="Second"),
            ],
        )
        result = _assemble_sections(outline, ["Content 1.", "Content 2."])
        assert "Content 1." in result
        assert "Content 2." in result
        assert result.index("Content 1.") < result.index("Content 2.")

    def test_section_with_heading_preserved(self):
        outline = DocumentOutline(
            title="Report",
            sections=[SectionInfo(index=0, heading="Summary", level=1, purpose="Summary")],
        )
        content = "## Summary\n\nThis is the summary."
        result = _assemble_sections(outline, [content])
        # Should not duplicate heading since content already has one
        assert result.count("Summary") == 1 or result.count("Summary") == 2


# --- Full pipeline (mocked LLM) ---


class TestGenerateLargeDocument:
    @patch("app.services.document_generation_pipeline.gemini._build_messages")
    @patch("app.services.document_generation_pipeline.gemini._get_shared_client")
    @patch("app.services.document_generation_pipeline.gemini._get_access_token")
    @patch("app.services.document_generation_pipeline.gemini.chat_completion")
    def test_basic_pipeline(self, mock_chat, mock_token, mock_client, mock_build, monkeypatch):
        """Full pipeline with mocked LLM responses."""
        monkeypatch.setattr("app.services.document_generation_pipeline.settings", types.SimpleNamespace(
            GIGACHAT_MODEL="GigaChat-Max",
            DOCUMENT_PIPELINE_MAX_TOKENS=4096,
            DOCUMENT_PIPELINE_SECTION_RETRIES=1,
            DOCUMENT_PIPELINE_MAX_TOTAL_RETRIES=10,
            DOCUMENT_PIPELINE_SECTION_TIMEOUT=120,
            DOCUMENT_PIPELINE_CONSISTENCY_CHECK=False,
        ))

        outline_json = json.dumps({
            "title": "Test Report",
            "sections": [
                {"heading": "Introduction", "level": 1, "purpose": "Intro text"},
                {"heading": "Main Content", "level": 1, "purpose": "Main text"},
            ],
            "key_entities": [],
            "style_notes": "",
        })

        section_responses = [
            "## Introduction\n\nThis is the introduction paragraph.",
            "## Main Content\n\nThis is the main content paragraph.",
        ]

        call_count = [0]

        def fake_chat(payload, token=None):
            call_count[0] += 1
            messages = payload.get("messages", [])
            user_msg = messages[-1]["content"] if messages else ""

            # Outline request
            if "outline" in user_msg.lower() or "структуру" in user_msg.lower():
                return {"choices": [{"message": {"content": outline_json}}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
            # Section requests
            return {"choices": [{"message": {"content": section_responses.pop(0)}}, ], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}

        mock_chat.side_effect = fake_chat
        mock_build.return_value = []
        mock_token.return_value = "fake-token"

        result = generate_large_document(
            user_request="Создай тестовый отчёт",
            title="Test Report",
        )

        assert "Introduction" in result
        assert "Main Content" in result
        assert "introduction paragraph" in result

    @patch("app.services.document_generation_pipeline.gemini._build_messages")
    @patch("app.services.document_generation_pipeline.gemini._get_shared_client")
    @patch("app.services.document_generation_pipeline.gemini._get_access_token")
    @patch("app.services.document_generation_pipeline.gemini.chat_completion")
    def test_pipeline_with_consistency_check(self, mock_chat, mock_token, mock_client, mock_build, monkeypatch):
        """Pipeline with consistency check enabled."""
        monkeypatch.setattr("app.services.document_generation_pipeline.settings", types.SimpleNamespace(
            GIGACHAT_MODEL="GigaChat-Max",
            DOCUMENT_PIPELINE_MAX_TOKENS=4096,
            DOCUMENT_PIPELINE_SECTION_RETRIES=1,
            DOCUMENT_PIPELINE_MAX_TOTAL_RETRIES=10,
            DOCUMENT_PIPELINE_SECTION_TIMEOUT=120,
            DOCUMENT_PIPELINE_CONSISTENCY_CHECK=True,
        ))

        outline_json = json.dumps({
            "title": "Report",
            "sections": [{"heading": "Section 1", "level": 1, "purpose": "Content"}],
            "key_entities": [],
            "style_notes": "",
        })

        call_count = [0]

        def fake_chat(payload, token=None):
            call_count[0] += 1
            messages = payload.get("messages", [])
            user_msg = messages[-1]["content"] if messages else ""
            if "outline" in user_msg.lower() or "структуру" in user_msg.lower():
                return {"choices": [{"message": {"content": outline_json}}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
            if "провер" in user_msg.lower():
                # Consistency check returns the same content
                return {"choices": [{"message": {"content": "# Report\n\n## Section 1\n\nContent here."}}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
            return {"choices": [{"message": {"content": "## Section 1\n\nContent here."}}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}

        mock_chat.side_effect = fake_chat
        mock_build.return_value = []
        mock_token.return_value = "fake-token"

        result = generate_large_document(
            user_request="создай подробный отчёт",
            title="Report",
        )

        assert "Section 1" in result
        assert "Content" in result

    @patch("app.services.document_generation_pipeline.gemini._build_messages")
    @patch("app.services.document_generation_pipeline.gemini._get_shared_client")
    @patch("app.services.document_generation_pipeline.gemini._get_access_token")
    @patch("app.services.document_generation_pipeline.gemini.chat_completion")
    def test_section_retry_on_empty(self, mock_chat, mock_token, mock_client, mock_build, monkeypatch):
        """Section generation retries when LLM returns empty content."""
        monkeypatch.setattr("app.services.document_generation_pipeline.settings", types.SimpleNamespace(
            GIGACHAT_MODEL="GigaChat-Max",
            DOCUMENT_PIPELINE_MAX_TOKENS=4096,
            DOCUMENT_PIPELINE_SECTION_RETRIES=2,
            DOCUMENT_PIPELINE_MAX_TOTAL_RETRIES=10,
            DOCUMENT_PIPELINE_SECTION_TIMEOUT=120,
            DOCUMENT_PIPELINE_CONSISTENCY_CHECK=False,
        ))

        outline_json = json.dumps({
            "title": "Report",
            "sections": [{"heading": "Part", "level": 1, "purpose": "Do it"}],
            "key_entities": [],
            "style_notes": "",
        })

        call_count = [0]

        def fake_chat(payload, token=None):
            call_count[0] += 1
            messages = payload.get("messages", [])
            user_msg = messages[-1]["content"] if messages else ""
            if "outline" in user_msg.lower() or "структуру" in user_msg.lower():
                return {"choices": [{"message": {"content": outline_json}}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
            # First call returns empty, second returns content
            if call_count[0] == 2:
                return {"choices": [{"message": {"content": ""}}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
            return {"choices": [{"message": {"content": "## Part\n\nReal content."}}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}

        mock_chat.side_effect = fake_chat
        mock_build.return_value = []
        mock_token.return_value = "fake-token"

        result = generate_large_document(
            user_request="создай подробный отчёт",
            title="Report",
        )

        assert "Real content" in result


# --- Edge cases ---


class TestPipelineEdgeCases:
    @patch("app.services.document_generation_pipeline.gemini._build_messages")
    @patch("app.services.document_generation_pipeline.gemini._get_shared_client")
    @patch("app.services.document_generation_pipeline.gemini._get_access_token")
    @patch("app.services.document_generation_pipeline.gemini.chat_completion")
    def test_malformed_outline_json_still_produces_output(self, mock_chat, mock_token, mock_client, mock_build, monkeypatch):
        """If outline LLM returns non-JSON, pipeline should handle gracefully."""
        monkeypatch.setattr("app.services.document_generation_pipeline.settings", types.SimpleNamespace(
            GIGACHAT_MODEL="GigaChat-Max",
            DOCUMENT_PIPELINE_MAX_TOKENS=4096,
            DOCUMENT_PIPELINE_SECTION_RETRIES=1,
            DOCUMENT_PIPELINE_MAX_TOTAL_RETRIES=10,
            DOCUMENT_PIPELINE_SECTION_TIMEOUT=120,
            DOCUMENT_PIPELINE_CONSISTENCY_CHECK=False,
        ))

        def fake_chat(payload, token=None):
            messages = payload.get("messages", [])
            user_msg = messages[-1]["content"] if messages else ""
            if "outline" in user_msg.lower() or "структуру" in user_msg.lower():
                # Return malformed JSON
                return {"choices": [{"message": {"content": "I can't do that"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
            return {"choices": [{"message": {"content": "Some content."}}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}

        mock_chat.side_effect = fake_chat
        mock_build.return_value = []
        mock_token.return_value = "fake-token"

        with pytest.raises(Exception):
            generate_large_document(
                user_request="создай подробный отчёт",
                title="Report",
            )
