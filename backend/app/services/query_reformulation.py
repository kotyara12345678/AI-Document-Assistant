"""Deterministic search-query reformulation.

Used by the agent's ``search_documents`` tool as a self-correction step: when
a query returns zero hits, instead of immediately answering "not found" the
agent tries a few rewritten variants of the query and searches again.

The reformulation has two phases:

1. **Entity-aware variants** (new): when the query contains structured entities
   (article numbers, INN, dates, contract numbers), generate targeted search
   variants that the exact-match and phrase layers can pick up.

2. **Generic stripping** (legacy): strip pleading verbs, drop stopwords, try
   single content tokens as a last resort.

Reformulation is ordered from most-like-original to most-stripped, and the
original query is never re-run (it already failed).
"""

from __future__ import annotations

import re

from app.services.entity_extraction import (
    extract_entities,
    generate_article_variants,
)

# Leading action verbs that add no retrieval information.
_LEADING_PLEADINGS = (
    "помоги найти документы про",
    "помоги найти",
    "покажи мне все документы",
    "покажи мне документы",
    "подскажи мне",
    "расскажи мне про",
    "расскажи мне о",
    "найди мне документы",
    "покажи мне",
    "расскажи мне",
    "найди мне",
    "помоги",
    "подскажи",
    "напомни",
    "покажи",
    "расскажи",
    "найди",
    "найти",
    "ищу",
    "искать",
    "нашел",
    "нашёл",
    "найду",
    "скажи",
)

# Russian and English function words that never carry document meaning.
_STOPWORDS = frozenset(
    """
    а без более бы был была были было будто бы в вам вас вдруг ведь во вот все
    всего всем всех всю вы да для до его ее ей ему еще ею же за из им ими их к
    как ко кто ли либо мне много мной моя мы над надо наконец нас не него нее
    ней нет ни них но ну о об один он она они оно от перед по под после при про
    раз сам свое своих себя сейчас снова со так такой там те тобой того той только
    том тот тою ту ты у уже хотя чего чем чему что чтобы чьи чья чье чьего чьей
    эти этими этим этих эту это я a an and are as at be by for from in is it of on
    or that the this to was were will with
    """.split()
)

_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def _tokenize(query: str) -> list[str]:
    """Lowercased content tokens, English/Russian digits included."""
    return [t.lower() for t in _TOKEN_RE.findall(query or "")]


def _dedupe(variants: list[str]) -> list[str]:
    """Drop case-insensitive duplicates and empty strings, preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for variant in variants:
        key = " ".join(variant.lower().split())
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(" ".join(variant.split()))
    return result


def _entity_variants(query: str) -> list[str]:
    """Generate entity-aware search variants from the query.

    For article numbers: "статья 105", "ст. 105", "статьи 105", "105".
    For INN: the raw digits.
    For dates: the raw date string.
    For contract numbers: "договор 4817", "4817", "№ 4817".
    """
    entities = extract_entities(query)
    variants: list[str] = []

    # Article number variants
    for num in entities.article_numbers:
        variants.extend(generate_article_variants(num))

    # INN variants
    for inn in entities.inn_values:
        variants.append(inn)

    # Contract number variants
    for cn in entities.contract_numbers:
        variants.append(f"договор {cn}")
        variants.append(cn)
        variants.append(f"№ {cn}")

    # Date variants
    for date in entities.dates:
        variants.append(date)

    # Phone variants
    for phone in entities.phone_numbers:
        variants.append(phone)

    # Email variants
    for email in entities.emails:
        variants.append(email)

    return variants


def reformulate_query(query: str, max_variants: int = 6) -> list[str]:
    """Return rewritten query variants ordered from most to least specific.

    ``query`` itself is NOT included — callers run it first. ``max_variants``
    caps the number of re-searches a caller will perform. Returns [] for a
    blank query or one that has nothing left after stripping.
    """
    tokens = _tokenize(query)
    if not tokens:
        return []

    variants: list[str] = []

    # Phase 1: Entity-aware variants (highest priority).
    entity_vars = _entity_variants(query)
    variants.extend(entity_vars)

    # Phase 2: Generic stripping.
    # Strip leading pleading verbs ("найди мне ..." -> "...").
    stripped = " ".join(tokens)
    for verb in _LEADING_PLEADINGS:
        verb_tokens = _tokenize(verb)
        if tokens[: len(verb_tokens)] == verb_tokens:
            stripped = " ".join(tokens[len(verb_tokens) :])
            break

    # Drop function words from the stripped form, keeping content terms.
    content = [t for t in _tokenize(stripped) if t not in _STOPWORDS]
    if not content:
        content = _tokenize(stripped)

    variants.append(stripped)
    if content != _tokenize(stripped):
        variants.append(" ".join(content))

    # Single content tokens as a last resort, most distinctive first.
    for token in sorted(set(content), key=lambda t: (-len(t), t)):
        if len(variants) >= max_variants:
            break
        variants.append(token)

    # Always try digit-only tokens even if they lost in the length sort.
    for token in sorted(set(content)):
        if len(variants) >= max_variants:
            break
        if token.isdigit() and token not in variants:
            variants.append(token)

    return _dedupe([v for v in variants if v])[:max_variants]
