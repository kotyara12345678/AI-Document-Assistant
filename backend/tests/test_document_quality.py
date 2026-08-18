"""Tests for the document output quality gate (app.services.document_quality).

Focus: unfilled placeholders must be detected, while ordinary bracketed text
(citations, cross-references) and explicit template requests are NOT flagged.
"""

import pytest

from app.services.document_quality import (
    find_placeholders,
    format_placeholder_warning,
    is_template_request,
)


def test_clean_document_has_no_placeholders():
    text = (
        "# Трудовой договор\n"
        "Между ООО «Альфа» и Ивановым Иваном заключён настоящий договор. "
        "Оклад составляет 200000 рублей. Срок действия — 1 год."
    )
    assert find_placeholders(text) == []


def test_bare_todo_tbd_na_are_critical():
    assert find_placeholders("TODO: заполнить реквизиты") == ["TODO"]
    assert find_placeholders("Оплата: TBD") == ["TBD"]
    assert find_placeholders("Телефон: N/A") == ["N/A"]


def test_double_braces_are_always_critical():
    assert find_placeholders("Оклад {{SALARY}}") == ["{{SALARY}}"]
    assert find_placeholders("Дата {{start_date}} и {{end_date}}") == [
        "{{start_date}}",
        "{{end_date}}",
    ]


def test_known_placeholder_words_inside_brackets():
    assert find_placeholders("Дата подписания: [дата]") == ["[дата]"]
    assert find_placeholders("Подписи сторон: [подписи]") == ["[подписи]"]
    assert find_placeholders("Сумма: [сумма]") == ["[сумма]"]
    assert find_placeholders("Сумма: [сумма договора]") == ["[сумма договора]"]
    assert find_placeholders("Реквизиты: [не указано]") == ["[не указано]"]
    assert find_placeholders("Name: [name]") == ["[name]"]


def test_ordinary_brackets_are_not_placeholders():
    # Citations, cross-references and numbered brackets must NOT be flagged.
    assert find_placeholders("См. [1] и [Приложение А]") == []
    assert find_placeholders("Пункт 3.2 [Важно]") == []


def test_placeholders_deduped_and_ordered():
    assert find_placeholders("[дата] и [ДАТА]") == ["[дата]"]


def test_empty_and_none_input():
    assert find_placeholders("") == []
    assert find_placeholders(None) == []


@pytest.mark.parametrize(
    "question,expected",
    [
        ("создай договор", False),
        ("создай трудовой договор по шаблону", True),
        ("нужен образец договора аренды", True),
        ("сделай типовой договор", True),
        ("сгенерируй готовый договор на 1 год", False),
        ("", False),
    ],
)
def test_is_template_request(question, expected):
    assert is_template_request(question) is expected


def test_format_placeholder_warning():
    warning = format_placeholder_warning(["[дата]", "[сумма]"])
    assert "[дата]" in warning
    assert "[сумма]" in warning
    assert format_placeholder_warning([]) == ""
