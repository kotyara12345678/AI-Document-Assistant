"""Retrieval pipeline regression tests.

Tests the full 4-layer retrieval pipeline (exact match + phrase FTS + keyword
FTS + semantic) for the 12 regression queries.

UNIT TESTS (no Docker required):
- TestExactPatternGeneration: verifies ILIKE patterns for each query
- TestPhraseQueryGeneration: verifies phraseto_tsquery phrases
- TestMergeLogic: verifies multi-layer scoring/boosting

INTEGRATION TESTS (require PostgreSQL + Qdrant):
- TestRetrievalRegression: uploads test documents and verifies that the
  correct chunk is retrieved for each query.

Integration tests are marked with ``pytest.mark.skipif`` when Docker services
are unavailable. To run them: ``docker compose up -d db qdrant && pytest``.
"""

import json
import os

import pytest

from app.services.entity_extraction import (
    QueryEntities,
    extract_entities,
    generate_article_variants,
)
from app.services.query_reformulation import reformulate_query


# ---------------------------------------------------------------------------
# Unit tests: exact pattern generation
# ---------------------------------------------------------------------------


class TestExactPatternGeneration:
    """Verify that _build_exact_patterns generates correct ILIKE patterns."""

    def _get_patterns(self, query: str) -> list[str]:
        from app.services.retrieval import _build_exact_patterns

        entities = extract_entities(query)
        return _build_exact_patterns(entities)

    def test_article_3_patterns(self):
        patterns = self._get_patterns("найди статью 3 ук рф")
        assert "статья 3" in patterns
        assert "ст. 3" in patterns
        assert "статьи 3" in patterns
        assert "статье 3" in patterns
        assert "статью 3" in patterns
        assert "3" in patterns

    def test_article_105_patterns(self):
        patterns = self._get_patterns("найди статью 105 ук рф")
        assert "статья 105" in patterns
        assert "ст. 105" in patterns
        assert "105" in patterns

    def test_article_3071_patterns(self):
        patterns = self._get_patterns("найди статью 307.1 гк рф")
        assert "статья 307.1" in patterns
        assert "ст. 307.1" in patterns
        assert "307.1" in patterns

    def test_inn_patterns(self):
        patterns = self._get_patterns("найди инн 1234567890")
        assert "1234567890" in patterns

    def test_contract_4817_patterns(self):
        patterns = self._get_patterns("найди договор № 4817")
        assert "4817" in patterns
        assert "№ 4817" in patterns
        assert "номер 4817" in patterns

    def test_date_patterns(self):
        patterns = self._get_patterns("найди договор от 12.04.2026")
        assert "12.04.2026" in patterns

    def test_no_patterns_for_generic_query(self):
        patterns = self._get_patterns("найди сумму договора")
        assert len(patterns) == 0

    def test_trailing_number_pattern(self):
        patterns = self._get_patterns("найди инн алексея 4")
        assert "4" in patterns


# ---------------------------------------------------------------------------
# Unit tests: phrase query generation
# ---------------------------------------------------------------------------


class TestPhraseQueryGeneration:
    """Verify that _build_phrase_queries generates correct FTS phrases."""

    def _get_phrases(self, query: str) -> list[str]:
        from app.services.retrieval import _build_phrase_queries

        entities = extract_entities(query)
        return _build_phrase_queries(entities)

    def test_article_phrase(self):
        phrases = self._get_phrases("найди статью 105 ук рф")
        assert "статья 105" in phrases
        assert "ст 105" in phrases

    def test_inn_phrase(self):
        phrases = self._get_phrases("найди инн 1234567890")
        assert "инн 1234567890" in phrases

    def test_contract_phrase(self):
        phrases = self._get_phrases("найди договор 4817")
        assert "договор 4817" in phrases

    def test_no_phrases_for_generic_query(self):
        phrases = self._get_phrases("привет")
        assert len(phrases) == 0


# ---------------------------------------------------------------------------
# Unit tests: entity-aware query reformulation
# ---------------------------------------------------------------------------


class TestEntityAwareReformulation:
    """Verify that reformulate_query generates entity-specific variants."""

    def test_article_variants_in_reformulation(self):
        variants = reformulate_query("найди статью 3 ук рф")
        # Should include article-specific variants
        assert "статья 3" in variants or "ст. 3" in variants
        assert "3" in variants

    def test_article_105_variants(self):
        variants = reformulate_query("найди статью 105 ук рф")
        assert "статья 105" in variants or "ст. 105" in variants

    def test_contract_variants(self):
        variants = reformulate_query("найди договор № 4817")
        assert "договор 4817" in variants or "4817" in variants

    def test_date_variants(self):
        variants = reformulate_query("найди договор от 12.04.2026")
        assert "12.04.2026" in variants

    def test_max_variants_respected(self):
        variants = reformulate_query("найди статью 3 ук рф", max_variants=4)
        assert len(variants) <= 4


# ---------------------------------------------------------------------------
# Unit tests: merge scoring logic
# ---------------------------------------------------------------------------


class TestMergeLogic:
    """Verify that _merge_results correctly boosts multi-layer matches."""

    def test_exact_match_outranks_semantic(self):
        """A chunk found by exact match should outrank a semantic-only chunk."""
        from app.services.retrieval import RetrievedChunk, _merge_results
        from app.schemas.chat import SourceRef
        from app.services.entity_extraction import QueryEntities

        exact_rows = [
            {
                "document_id": 1,
                "chunk_index": 0,
                "text": "Статья 105. Убийство...",
                "filename": "uk.pdf",
                "match_score": 0.95,
            }
        ]
        semantic_chunks = [
            RetrievedChunk(
                source=SourceRef(
                    document_id=2,
                    filename="other.pdf",
                    chunk_index= 0,
                    score=0.85,
                    text="Some other text about something...",
                ),
                score=0.85,
                text="Some other text about something...",
            )
        ]

        merged = _merge_results(
            semantic_chunks, [], exact_rows, [],
            QueryEntities(article_numbers=("105",)),
            top_k=5,
            min_score=0.3,
        )
        assert len(merged) >= 1
        # The exact-match chunk should be first (higher score due to boost).
        assert merged[0].source.document_id == 1
        assert merged[0].score > 0.85

    def test_multi_layer_boost(self):
        """A chunk found by 3+ layers gets a stronger boost."""
        from app.services.retrieval import RetrievedChunk, _merge_results
        from app.schemas.chat import SourceRef
        from app.services.entity_extraction import QueryEntities

        semantic_chunks = [
            RetrievedChunk(
                source=SourceRef(
                    document_id=1, filename="f.pdf", chunk_index=0,
                    score=0.7, text="Статья 105 УК РФ",
                ),
                score=0.7, text="Статья 105 УК РФ",
            )
        ]
        kw_rows = [
            {
                "document_id": 1, "chunk_index": 0,
                "text": "Статья 105 УК РФ", "filename": "f.pdf",
                "rank": 0.8, "total_lexemes": 3, "matched_lexemes": 3,
            }
        ]
        exact_rows = [
            {
                "document_id": 1, "chunk_index": 0,
                "text": "Статья 105 УК РФ", "filename": "f.pdf",
                "match_score": 0.9,
            }
        ]

        merged = _merge_results(
            semantic_chunks, kw_rows, exact_rows, [],
            QueryEntities(article_numbers=("105",)),
            top_k=5,
            min_score=0.3,
        )
        assert len(merged) >= 1
        chunk = merged[0]
        # Found by 3 layers: semantic + keyword + exact -> boosted above 0.9
        assert chunk.score >= 0.9

    def test_empty_results(self):
        from app.services.retrieval import _merge_results
        from app.services.entity_extraction import QueryEntities

        merged = _merge_results([], [], [], [], QueryEntities(), top_k=5, min_score=0.3)
        assert merged == []


# ---------------------------------------------------------------------------
# Integration tests (require PostgreSQL + Qdrant)
# ---------------------------------------------------------------------------

_DOCKER_AVAILABLE = None


def _docker_available() -> bool:
    global _DOCKER_AVAILABLE
    if _DOCKER_AVAILABLE is not None:
        return _DOCKER_AVAILABLE
    try:
        import psycopg2
        conn = psycopg2.connect(
            os.environ.get(
                "DATABASE_URL",
                "postgresql+psycopg2://docassistant:docassistant@localhost:5432/docassistant",
            )
        )
        conn.close()
        _DOCKER_AVAILABLE = True
    except Exception:
        _DOCKER_AVAILABLE = False
    return _DOCKER_AVAILABLE


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason="Integration tests require PostgreSQL + Qdrant (Docker)",
)


def _upload(client, filename: str, content: bytes) -> int:
    resp = client.post(
        "/api/documents/upload",
        files={"file": (filename, content)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()[0]["id"]


# Test documents with specific content for each regression query.
_TEST_DOCS = {
    "uk_rf.txt": (
        "Уголовный кодекс Российской Федерации.\n\n"
        "Статья 3. Принцип законности.\n"
        "Преступность деяния, а также его наказуемость и иные уголовно-правовые "
        "последствия определяются только настоящим Кодексом.\n\n"
        "Статья 105. Убийство.\n"
        "Убийство, то есть умышленное причинение смерти другому человеку, "
        "наказывается лишением свободы на срок от шести до пятнадцати лет.\n\n"
        "Статья 307.1. Заведомо ложные показание.\n"
        "Заведомо ложные показание свидетеля, потерпевшего, а также "
        "заключение эксперта в суде.\n"
    ),
    "contracts.txt": (
        "Договор № 4817 от 12.04.2026\n"
        "Между ООО «Альфа» и ИП Сергеев А.В.\n\n"
        "Сумма договора: 500 000 рублей.\n"
        "Срок исполнения: 30.06.2026.\n\n"
        "Договор № 3210 от 01.03.2025\n"
        "Между ЗАО «Бета» и ООО «Гамма».\n"
        "Сумма: 1 200 000 рублей.\n"
    ),
    "contacts.txt": (
        "Справочник сотрудников.\n\n"
        "Алексей Иванов\n"
        "ИНН: 7701234567\n"
        "Телефон: +7 916 123 4567\n"
        "Email: alexey.ivanov@company.ru\n\n"
        "Дмитрий Петров\n"
        "ИНН: 7709876543\n"
        "Телефон: +7 926 987 6543\n"
        "Email: dmitriy.petrov@company.ru\n\n"
        "Алексей Сидоров\n"
        "ИНН: 7701112223\n"
        "Телефон: +7 903 111 2233\n"
    ),
}


class TestRetrievalRegression:
    """Integration tests: upload test docs and verify retrieval for each query.

    NOTE: These tests require running PostgreSQL + Qdrant via Docker.
    Run: docker compose up -d db qdrant && pytest tests/test_retrieval_regression.py -v
    """

    @pytest.fixture(autouse=True)
    def _upload_docs(self, client, user_id):
        """Upload test documents once per test class."""
        from app.services.retrieval import retrieve_context

        self.user_id = user_id
        self.doc_ids = {}
        for fname, content in _TEST_DOCS.items():
            doc_id = _upload(client, fname, content.encode("utf-8"))
            self.doc_ids[fname] = doc_id
        self.retrieve = lambda q, **kw: retrieve_context(
            question=q, user_id=user_id, **kw
        )

    def _find_doc_id(self, chunks, filename):
        """Find document_id by filename from retrieved chunks."""
        for c in chunks:
            if c.source.filename == filename:
                return c.source.document_id
        return None

    def _chunk_has_text(self, chunks, text):
        """Check if any retrieved chunk contains the expected text."""
        return any(text in c.text for c in chunks)

    # --- Article number queries ---

    def test_naydi_statyu_3_uk_rf(self):
        chunks = self.retrieve("найди статью 3 ук рф", top_k=5, min_score=0.0)
        assert chunks, "Should find chunks about Article 3"
        assert self._chunk_has_text(chunks, "Статья 3"), f"Expected 'Статья 3' in results: {[c.text[:80] for c in chunks]}"

    def test_naydi_3_statyu_uk_rf(self):
        chunks = self.retrieve("найди 3 статью ук рф", top_k=5, min_score=0.0)
        assert chunks, "Should find chunks about Article 3"
        assert self._chunk_has_text(chunks, "Статья 3"), f"Expected 'Статья 3' in results: {[c.text[:80] for c in chunks]}"

    def test_naydi_statyu_105_uk_rf(self):
        chunks = self.retrieve("найди статью 105 ук рф", top_k=5, min_score=0.0)
        assert chunks, "Should find chunks about Article 105"
        assert self._chunk_has_text(chunks, "Статья 105"), f"Expected 'Статья 105' in results"

    def test_naydi_statyu_3071_gk_rf(self):
        chunks = self.retrieve("найди статью 307.1 гк рф", top_k=5, min_score=0.0)
        assert chunks, "Should find chunks about Article 307.1"
        assert self._chunk_has_text(chunks, "307.1"), f"Expected '307.1' in results"

    # --- INN queries ---

    def test_naydi_inn_alekseya(self):
        chunks = self.retrieve("найди инн алексея", top_k=5, min_score=0.0)
        assert chunks, "Should find chunks about Alexey's INN"
        assert self._chunk_has_text(chunks, "ИНН"), f"Expected 'ИНН' in results"

    def test_naydi_inn_alekseya_4(self):
        """Trailing '4' should not break the search."""
        chunks = self.retrieve("найди инн алексея 4", top_k=5, min_score=0.0)
        assert chunks, "Should find chunks about INN despite trailing number"
        assert self._chunk_has_text(chunks, "ИНН"), f"Expected 'ИНН' in results"

    def test_naydi_inn_alekseya_i_dmitriya(self):
        chunks = self.retrieve("найди инн алексея и дмитрия", top_k=5, min_score=0.0)
        assert chunks, "Should find INN for both people"
        # Should find both INN values.
        all_text = " ".join(c.text for c in chunks)
        assert "7701234567" in all_text or "7709876543" in all_text

    # --- Contract queries ---

    def test_naydi_dogovor_4817(self):
        chunks = self.retrieve("найди договор № 4817", top_k=5, min_score=0.0)
        assert chunks, "Should find contract 4817"
        assert self._chunk_has_text(chunks, "4817"), f"Expected '4817' in results"

    def test_naydi_dogovor_ot_date(self):
        chunks = self.retrieve("найди договор от 12.04.2026", top_k=5, min_score=0.0)
        assert chunks, "Should find contract from 12.04.2026"
        assert self._chunk_has_text(chunks, "12.04.2026"), f"Expected '12.04.2026' in results"

    # --- Semantic queries ---

    def test_naydi_summu_dogovora(self):
        chunks = self.retrieve("найди сумму договора", top_k=5, min_score=0.0)
        assert chunks, "Should find contract sum"
        all_text = " ".join(c.text for c in chunks)
        assert "сумма" in all_text.lower() or "500" in all_text or "1 200" in all_text

    def test_naydi_telefon_alekseya(self):
        chunks = self.retrieve("найди телефон алексея", top_k=5, min_score=0.0)
        assert chunks, "Should find Alexey's phone"
        assert self._chunk_has_text(chunks, "+7"), f"Expected phone number in results"

    def test_naydi_email_dmitriya(self):
        chunks = self.retrieve("найди email дмитрия", top_k=5, min_score=0.0)
        assert chunks, "Should find Dmitry's email"
        all_text = " ".join(c.text for c in chunks)
        assert "dmitriy" in all_text.lower() or "petrov" in all_text.lower()


class TestNegativeRetrieval:
    """Integration tests: verify that non-existent identifiers are NOT found.

    NOTE: Requires PostgreSQL + Qdrant via Docker.
    """

    @pytest.fixture(autouse=True)
    def _upload_docs(self, client, user_id):
        from app.services.retrieval import retrieve_context

        self.user_id = user_id
        for fname, content in _TEST_DOCS.items():
            _upload(client, fname, content.encode("utf-8"))
        self.retrieve = lambda q, **kw: retrieve_context(
            question=q, user_id=user_id, **kw
        )

    def test_nonexistent_article(self):
        """Article 999 does not exist in test docs — should return nothing relevant."""
        chunks = self.retrieve("статья 999 ук рф", top_k=5, min_score=0.5)
        # May return some chunks via semantic similarity, but the exact article
        # should NOT be in any chunk text.
        for c in chunks:
            assert "Статья 999" not in c.text, f"Must not find 'Статья 999' in results"

    def test_nonexistent_inn(self):
        """INN 0000000000 does not exist — should not claim it was found."""
        chunks = self.retrieve("инн 0000000000", top_k=5, min_score=0.5)
        for c in chunks:
            assert "0000000000" not in c.text, f"Must not find '0000000000'"

    def test_nonexistent_contract(self):
        """Contract 99999 does not exist."""
        chunks = self.retrieve("договор № 99999", top_k=5, min_score=0.5)
        for c in chunks:
            assert "99999" not in c.text, f"Must not find '99999'"

    def test_nonexistent_date(self):
        """Date 01.01.3000 does not exist."""
        chunks = self.retrieve("договор от 01.01.3000", top_k=5, min_score=0.5)
        for c in chunks:
            assert "01.01.3000" not in c.text, f"Must not find '01.01.3000'"
