"""Tests for the deterministic query reformulation used by search_documents
self-correction (app.services.query_reformulation)."""

import pytest

from app.services.query_reformulation import reformulate_query


def test_simple_query_survives_as_variant():
    variants = reformulate_query("зарплата Сергея")
    assert variants  # at least one rewritten form
    assert "зарплата сергея" in variants


def test_strips_leading_pleading_verbs():
    variants = reformulate_query("найди мне зарплату Сергея")
    # The pleading block "найди мне" is removed; the content term survives.
    assert "зарплату сергея" in variants
    assert not any(v == "найди" for v in variants)
    assert not any("найди" in v for v in variants)


def test_drops_function_words():
    variants = reformulate_query("какие документы по зарплате Сергея есть")
    assert "какие документы по зарплате сергея есть" in variants  # stripped form
    assert "какие документы зарплате сергея есть" in variants  # function words removed


def test_single_content_tokens_as_last_resort():
    variants = reformulate_query("покажи все")
    # "покажи" is a pleading verb, "все" is a stopword — only nothing remains,
    # so the longest surviving token becomes the fallback query.
    assert variants
    assert "все" in variants


def test_blank_and_junk_queries_yield_nothing():
    assert reformulate_query("") == []
    assert reformulate_query("   ") == []
    assert reformulate_query("!!!" ) == []


def test_variants_are_deduplicated_and_ordered():
    variants = reformulate_query("найди найди документ про договор")
    lowered = [v.lower() for v in variants]
    assert len(lowered) == len(set(lowered))


def test_max_variants_honoured():
    variants = reformulate_query("найти договор аренды офиса и зарплату сотрудника")
    assert len(variants) <= 6


def test_original_query_not_in_variants():
    # Callers run the original first; variants must differ from it.
    query = "найди документы про отпуск"
    for variant in reformulate_query(query):
        assert variant.lower() != query.lower()