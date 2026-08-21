"""Unit tests for entity extraction from search queries.

These tests verify that the extraction module correctly identifies:
- Article numbers (статья N, ст. N)
- INN values (10/12 digits)
- Dates (DD.MM.YYYY)
- Contract numbers (№ N, договор N)
- Phone numbers
- Email addresses
- Organizations
- Exact phrases (quoted text)

All tests are pure unit tests — no DB, no Docker required.
"""

import pytest

from app.services.entity_extraction import (
    QueryEntities,
    extract_entities,
    generate_article_variants,
)


class TestArticleExtraction:
    def test_basic_article_number(self):
        e = extract_entities("найди статью 3 ук рф")
        assert "3" in e.article_numbers

    def test_article_with_decimal(self):
        e = extract_entities("найди статью 307.1 гк рф")
        assert "307.1" in e.article_numbers

    def test_article_st_dot(self):
        e = extract_entities("ст. 105 ук рф")
        assert "105" in e.article_numbers

    def test_article_genitive(self):
        e = extract_entities("найди статьи 105 ук рф")
        assert "105" in e.article_numbers

    def test_article_prepositional(self):
        e = extract_entities("в статье 3 тк рф")
        assert "3" in e.article_numbers

    def test_article_accusative(self):
        e = extract_entities("найди 3 статью ук рф")
        assert "3" in e.article_numbers

    def test_article_no_false_positive(self):
        e = extract_entities("привет как дела")
        assert len(e.article_numbers) == 0


class TestINNExtraction:
    def test_inn_10_digits(self):
        e = extract_entities("найди инн алексея 1234567890")
        assert "1234567890" in e.inn_values

    def test_inn_12_digits(self):
        e = extract_entities("инн 123456789012")
        assert "123456789012" in e.inn_values

    def test_inn_with_prefix(self):
        e = extract_entities("инн: 1234567890")
        assert "1234567890" in e.inn_values

    def test_inn_no_false_positive_short(self):
        e = extract_entities("найди 3 документа")
        assert len(e.inn_values) == 0


class TestDateExtraction:
    def test_date_dd_mm_yyyy(self):
        e = extract_entities("договор от 12.04.2026")
        assert "12.04.2026" in e.dates

    def test_date_dd_mm_yy(self):
        e = extract_entities("дата 01.01.25")
        assert "01.01.25" in e.dates

    def test_date_slash(self):
        e = extract_entities("от 12/04/2026")
        assert "12/04/2026" in e.dates


class TestContractNumberExtraction:
    def test_contract_hash(self):
        e = extract_entities("договор № 4817")
        assert "4817" in e.contract_numbers

    def test_contract_number_word(self):
        e = extract_entities("номер договора 4817")
        assert "4817" in e.contract_numbers

    def test_contract_bare(self):
        e = extract_entities("договор 4817 от 12.04.2026")
        assert "4817" in e.contract_numbers

    def test_contract_with_slash(self):
        e = extract_entities("договор № 4817/2026")
        assert "4817/2026" in e.contract_numbers


class TestPhoneExtraction:
    def test_phone_with_prefix(self):
        e = extract_entities("телефон +7 916 123 4567")
        assert len(e.phone_numbers) > 0

    def test_phone_tel(self):
        e = extract_entities("тел. 89161234567")
        assert len(e.phone_numbers) > 0


class TestEmailExtraction:
    def test_email_basic(self):
        e = extract_entities("email dmitriy@example.com")
        assert "dmitriy@example.com" in e.emails

    def test_email_complex(self):
        e = extract_entities("почта user.name+tag@domain.co.uk")
        assert "user.name+tag@domain.co.uk" in e.emails


class TestOrganizationExtraction:
    def test_ooo(self):
        e = extract_entities("ООО «Альфа»")
        assert len(e.organizations) > 0
        assert "ООО" in e.organizations[0]

    def test_zao(self):
        e = extract_entities("ЗАО \"Рога и Копыта\"")
        assert len(e.organizations) > 0


class TestExactPhrases:
    def test_quoted_phrase(self):
        e = extract_entities('найди "трудовой договор"')
        assert "трудовой договор" in e.exact_phrases

    def test_guillemet_phrase(self):
        e = extract_entities("найди «зарплата Сергея»")
        assert "зарплата Сергея" in e.exact_phrases


class TestHasExact:
    def test_has_exact_true(self):
        e = extract_entities("найди статью 3 ук рф")
        assert e.has_exact is True

    def test_has_exact_false(self):
        e = extract_entities("привет")
        assert e.has_exact is False


class TestAllIdentifiers:
    def test_priority_order(self):
        e = extract_entities("найди статью 3 инн 1234567890 договор 4817")
        ids = e.all_identifiers
        # Articles first, then INN, then contract
        assert ids.index("3") < ids.index("1234567890")
        assert ids.index("1234567890") < ids.index("4817")


# ---------------------------------------------------------------------------
# Generate article variants
# ---------------------------------------------------------------------------


class TestArticleVariants:
    def test_basic_variants(self):
        variants = generate_article_variants("105")
        assert "статья 105" in variants
        assert "ст. 105" in variants
        assert "статьи 105" in variants
        assert "статье 105" in variants
        assert "статью 105" in variants
        assert "105" in variants

    def test_decimal_variants(self):
        variants = generate_article_variants("307.1")
        assert "статья 307.1" in variants
        assert "ст. 307.1" in variants


# ---------------------------------------------------------------------------
# Full regression query extraction
# ---------------------------------------------------------------------------


class TestRegressionQueryExtraction:
    """Verify entity extraction for each of the 12 regression queries."""

    def test_naydi_inn_alekseya(self):
        e = extract_entities("найди инн алексея")
        assert len(e.inn_values) == 0  # no actual INN number in query
        assert e.has_exact is False  # just a name, no identifiers

    def test_naydi_inn_alekseya_4(self):
        e = extract_entities("найди инн алексея 4")
        assert len(e.inn_values) == 0  # "4" is too short for INN
        assert "4" in e.exact_numbers

    def test_naydi_inn_alekseya_i_dmitriya(self):
        e = extract_entities("найди инн алексея и дмитрия")
        assert len(e.inn_values) == 0
        assert e.has_exact is False

    def test_naydi_statyu_3_uk_rf(self):
        e = extract_entities("найди статью 3 ук рф")
        assert "3" in e.article_numbers
        assert e.has_exact is True

    def test_naydi_3_statyu_uk_rf(self):
        e = extract_entities("найди 3 статью ук рф")
        assert "3" in e.article_numbers
        assert e.has_exact is True

    def test_naydi_statyu_105_uk_rf(self):
        e = extract_entities("найди статью 105 ук рф")
        assert "105" in e.article_numbers
        assert e.has_exact is True

    def test_naydi_statyu_3071_gk_rf(self):
        e = extract_entities("найди статью 307.1 гк рф")
        assert "307.1" in e.article_numbers
        assert e.has_exact is True

    def test_naydi_dogovor_4817(self):
        e = extract_entities("найди договор № 4817")
        assert "4817" in e.contract_numbers
        assert e.has_exact is True

    def test_naydi_dogovor_ot_date(self):
        e = extract_entities("найди договор от 12.04.2026")
        assert "12.04.2026" in e.dates
        assert e.has_exact is True

    def test_naydi_summu_dogovora(self):
        e = extract_entities("найди сумму договора")
        assert e.has_exact is False  # no specific identifier

    def test_naydi_telefon_alekseya(self):
        e = extract_entities("найди телефон алексея")
        assert e.has_exact is False  # no actual phone number

    def test_naydi_email_dmitriya(self):
        e = extract_entities("найди email дмитрия")
        assert e.has_exact is False  # no actual email address
