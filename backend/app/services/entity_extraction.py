"""Entity extraction from search queries.

Extracts structured entities (article numbers, INN, dates, contract numbers,
phones, emails, organizations, numeric identifiers, **legal article law names**)
from natural-language Russian/English queries so the retrieval pipeline can run
targeted exact-match searches alongside semantic and FTS.

The extraction is purely regex-based — no ML, no network calls, deterministic
and fast.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Law / code name normalisation: abbreviations and full names
# ---------------------------------------------------------------------------

# Maps every recognised abbreviation (lowercase) to a list of search keywords
# that should appear in chunks belonging to that law.  The first element is the
# canonical short name used in query expansion (e.g. "УК", "ГК").
_LAW_ALIASES: dict[str, list[str]] = {
    "ук": ["УК", "уголовн", "уголовного кодекс"],
    "гк": ["ГК", "гражданск", "гражданского кодекс"],
    "гпк": ["ГПК", "гражданск процессуальн", "гражданского процессуальн"],
    "апк": ["АПК", "арбитражн процессуальн", "арбитражного процессуальн"],
    "коап": ["КоАП", "административн правонарушен", "кодексAdministrativн"],
    "коап_": ["КоАП", "кодекс об административных правонарушениях"],
    "тк": ["ТК", "трудов", "трудового кодекс"],
    "ск": ["СК", "семейн", "семейного кодекс"],
    "нк": ["НК", "налогов", "налогового кодекс"],
    "упк": ["УПК", "уголовн процессуальн", "уголовного процессуальн"],
    "кас": ["КАС", "административн судопроизводств", "кодекс административн судопроизводств"],
    "жк": ["ЖК", "жилищн", "жилищного кодекс"],
    "зк": ["ЗК", "земельн", "земельного кодекс"],
    "фз": ["ФЗ", "федеральн закон"],
}

# Reverse map: keyword fragment → canonical abbreviation
_KEYWORD_TO_ABBREV: dict[str, str] = {}
for _abbr, _keywords in _LAW_ALIASES.items():
    for _kw in _keywords[1:]:  # skip the canonical short name
        _KEYWORD_TO_ABBREV[_kw.lower()] = _abbr.rstrip("_")


# Full law name patterns (Russian): "Уголовный кодекс Российской Федерации"
# etc.  These are matched case-insensitively in the query text.
_FULL_LAW_NAME_RE = re.compile(
    r"(?:уголовн(?:ого|ый|ом|ую|ая|ой|ых|ому|ого)\s+"
    r"кодекс(?:\s+(?:Российск(?:ой|ая|ого|ую|ой|ие|их|ому|ой)\s+Федерации|РФ))?)|"
    r"(?:гражданск(?:ого|ий|ом|ую|ая|ой|их|ому|ого)\s+"
    r"кодекс(?:\s+(?:Российск(?:ой|ая|ого|ую|ой|ие|их|ому|ой)\s+Федерации|РФ))?)|"
    r"(?:трудов(?:ого|ой|ом|ую|ая|ой|ых|ому|ого)\s+"
    r"кодекс(?:\s+(?:Российск(?:ой|ая|ого|ую|ой|ие|их|ому|ой)\s+Федерации|РФ))?)|"
    r"(?:семейн(?:ого|ый|ом|ую|ая|ой|ых|ому|ого)\s+"
    r"кодекс(?:\s+(?:Российск(?:ой|ая|ого|ую|ой|ие|их|ому|ой)\s+Федерации|РФ))?)|"
    r"(?:налогов(?:ого|ой|ом|ую|ая|ой|ых|ому|ого)\s+"
    r"кодекс(?:\s+(?:Российск(?:ой|ая|ого|ую|ой|ие|их|ому|ой)\s+Федерации|РФ))?)|"
    r"(?:жилищн(?:ого|ий|ом|ую|ая|ой|ых|ому|ого)\s+"
    r"кодекс(?:\s+(?:Российск(?:ой|ая|ого|ую|ой|ие|их|ому|ой)\s+Федерации|РФ))?)|"
    r"(?:земельн(?:ого|ий|ом|ую|ая|ой|ых|ому|ого)\s+"
    r"кодекс(?:\s+(?:Российск(?:ой|ая|ого|ую|ой|ие|их|ому|ой)\s+Федерации|РФ))?)",
    re.IGNORECASE,
)

# Abbreviation + optional "РФ": "УК", "УК РФ", "ГК", "ГК РФ" etc.
_ABBREV_RE = re.compile(
    r"\b(ук|гк|гпк|апк|коап|тк|ск|нк|упк|кас|жк|зк)"
    r"(?:\s+(?:рф|Российск\w*\s+Федерации))?\b",
    re.IGNORECASE,
)


def _normalize_law(abbrev: str) -> str:
    """Normalise a law abbreviation to canonical lowercase form."""
    a = abbrev.strip().lower().rstrip("_")
    if a in _LAW_ALIASES:
        return a
    return a


def detect_law(query: str) -> str | None:
    """Detect a law/codec name from the query text.

    Returns the canonical abbreviation (e.g. "ук", "гк", "тк") or None.
    Tries full name matching first, then abbreviation matching.
    """
    q = query or ""

    # 1. Try full law name
    m = _FULL_LAW_NAME_RE.search(q)
    if m:
        matched = m.group(0).lower()
        for kw, abbr in _KEYWORD_TO_ABBREV.items():
            if kw in matched:
                return abbr
        # Fallback: check by keyword fragments
        if "уголовн" in matched:
            return "ук"
        if "гражданск" in matched:
            return "гк"
        if "трудов" in matched:
            return "тк"
        if "семейн" in matched:
            return "ск"
        if "налогов" in matched:
            return "нк"
        if "жилищн" in matched:
            return "жк"
        if "земельн" in matched:
            return "зк"

    # 2. Try abbreviation
    m = _ABBREV_RE.search(q)
    if m:
        return _normalize_law(m.group(1))

    return None


def get_law_keywords(law: str) -> list[str]:
    """Return search keywords for a law abbreviation.

    These are substrings that should appear in chunks belonging to that law.
    Used for exact-match pattern building and post-retrieval validation.
    """
    abbr = _normalize_law(law)
    keywords = _LAW_ALIASES.get(abbr, [])
    return list(keywords) if keywords else [law.upper()]


@dataclass(frozen=True)
class QueryEntities:
    """All entities extracted from a single query."""

    article_numbers: tuple[str, ...] = ()
    chapter_numbers: tuple[str, ...] = ()  # "глава 2" → ("2",)
    inn_values: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()
    contract_numbers: tuple[str, ...] = ()
    phone_numbers: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    organizations: tuple[str, ...] = ()
    exact_numbers: tuple[str, ...] = ()
    exact_phrases: tuple[str, ...] = ()
    law_name: str | None = None  # canonical law abbreviation (e.g. "ук", "гк")
    is_quote_request: bool = False  # "процитируй" detected

    @property
    def has_exact(self) -> bool:
        return bool(
            self.article_numbers
            or self.inn_values
            or self.dates
            or self.contract_numbers
            or self.phone_numbers
            or self.emails
            or self.organizations
            or self.exact_phrases
        )

    @property
    def all_identifiers(self) -> list[str]:
        """All raw identifier strings, in priority order."""
        out: list[str] = []
        for group in (
            self.article_numbers,
            self.inn_values,
            self.contract_numbers,
            self.dates,
            self.phone_numbers,
            self.emails,
            self.organizations,
            self.exact_numbers,
        ):
            out.extend(group)
        return out


# ---------------------------------------------------------------------------
# Russian article number patterns: "статья 3", "ст. 105.1", "статьи 307.1"
# ---------------------------------------------------------------------------

_ARTICLE_PREFIXES = (
    "стать[яеиюейюаы]",
    "ст\\.",
    "ст ",
)
_ARTICLE_RE = re.compile(
    r"(?:^|(?<=\s))(?:"
    + "|".join(_ARTICLE_PREFIXES)
    + r")\s+"
    r"(\d+(?:\.\d+)?)"
    r"(?:\s+(?:ук|гк|кзп|коап|апк|уип|тк|:UIControl|ЖК|ЗК|НК|СК|ФЗ))?",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Chapter patterns: "глава 2", "главу 5", "главы 3", "главе 7"
# ---------------------------------------------------------------------------

_CHAPTER_RE = re.compile(
    r"(?:^|(?<=\s))глав[ауыеё]\s+(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

# Quote request detection: "процитируй", "цитируй", "цитата"
_QUOTE_RE = re.compile(
    r"процитируй|цитируй|цитату|цитат[аеуыи]|процитировать",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# INN (Individual Taxpayer Number): 10 or 12 digits, optionally after "инн"
# ---------------------------------------------------------------------------

_INN_RE = re.compile(
    r"(?:инн\s*[:\s]?\s*)?(\d{10}(?:\d{2})?)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Dates: DD.MM.YYYY, DD/MM/YYYY, DD.MM.YY
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(
    r"(\d{1,2}[./]\d{1,2}[./]\d{2,4})",
)

# ---------------------------------------------------------------------------
# Contract numbers: "№ 4817", "номер 4817", "договор 4817/2026"
# ---------------------------------------------------------------------------

_CONTRACT_RE = re.compile(
    r"(?:№\s*|номер\s+|договор\s+(?:№\s*)?)(\d[\d/\-]*\d|\d+)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Phone phones: after "телефон", "тел.", "+7 ...", "8 ..."
# ---------------------------------------------------------------------------

_PHONE_RE = re.compile(
    r"(?:тел(?:ефон|\.|\s)|моб\.?\s*)?(\+?\d[\d\s\-\(\)]{7,}\d)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Email addresses
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
)

# ---------------------------------------------------------------------------
# Organization patterns: "ООО «Альфа»", "ЗАО Рога и Копыта"
# ---------------------------------------------------------------------------

_ORG_RE = re.compile(
    r"(ООО|ОАО|ЗАО|ПАО|ИП|АО|НКО|ТСЖ)\s+[«\"]([^»\"]+)[»\"]",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Generic number extraction (for trailing numbers in search queries)
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"(?<!\w)(\d+(?:\.\d+)?)(?!\w)")


def extract_entities(query: str) -> QueryEntities:
    """Extract all recognisable entities from a search query.

    Returns a QueryEntities dataclass with deduplicated tuples of raw strings.
    """
    if not query or not query.strip():
        return QueryEntities()

    # Article numbers
    articles = list(dict.fromkeys(m.group(1) for m in _ARTICLE_RE.finditer(query)))

    # Chapter numbers — but ONLY when the query explicitly says "глава/главу".
    # A bare number like "2" after "процитируй" is NOT a chapter reference;
    # it must be preceded by the word "глава" in some case form.
    chapters = list(dict.fromkeys(m.group(1) for m in _CHAPTER_RE.finditer(query)))

    # Quote request detection
    is_quote = bool(_QUOTE_RE.search(query))

    # INN
    inns: list[str] = []
    for m in _INN_RE.finditer(query):
        val = m.group(1)
        if len(val) in (10, 12):
            inns.append(val)
    inns = list(dict.fromkeys(inns))

    # Dates
    dates = list(dict.fromkeys(m.group(1) for m in _DATE_RE.finditer(query)))

    # Contract numbers
    contracts = list(dict.fromkeys(m.group(1) for m in _CONTRACT_RE.finditer(query)))

    # Phone phones
    phones = list(dict.fromkeys(m.group(1) for m in _PHONE_RE.finditer(query)))

    # Emails
    emails = list(dict.fromkeys(m.group(1) for m in _EMAIL_RE.finditer(query)))

    # Organizations
    orgs = list(
        dict.fromkeys(
            f"{m.group(1)} «{m.group(2)}»" for m in _ORG_RE.finditer(query)
        )
    )

    # All standalone numbers (used as fallback exact-match tokens)
    all_numbers = list(
        dict.fromkeys(m.group(1) for m in _NUMBER_RE.finditer(query))
    )
    # Exclude numbers already captured as article/INN/etc.
    captured = set()
    for group in (articles, inns, dates, contracts):
        captured.update(group)
    standalone_numbers = [n for n in all_numbers if n not in captured]

    # Exact phrases: multi-word quoted or parenthesised segments
    phrase_patterns = (
        re.compile(r"[«\"]([^»\"]+)[»\"]"),
        re.compile(r"\(([^)]{3,})\)"),
    )
    phrases: list[str] = []
    for pat in phrase_patterns:
        phrases.extend(m.group(1).strip() for m in pat.finditer(query))
    phrases = list(dict.fromkeys(phrases))

    return QueryEntities(
        article_numbers=tuple(articles),
        chapter_numbers=tuple(chapters),
        inn_values=tuple(inns),
        dates=tuple(dates),
        contract_numbers=tuple(contracts),
        phone_numbers=tuple(phones),
        emails=tuple(emails),
        organizations=tuple(orgs),
        exact_numbers=tuple(standalone_numbers),
        exact_phrases=tuple(phrases),
        law_name=detect_law(query),
        is_quote_request=is_quote,
    )


def generate_article_variants(
    article_number: str,
    law_name: str | None = None,
) -> list[str]:
    """Generate search variants for an article number, optionally scoped to a law.

    "105" → ["статья 105", "ст. 105", "статьи 105", "статье 105",
              "статью 105", "105"]

    When law_name is provided (e.g. "ук"), additional combined variants are
    prepended: "статья 105 УК", "ст. 105 УК" etc.  These combined patterns
    are more specific and score higher in exact-match searches.
    """
    num = article_number.strip()
    variants: list[str] = []

    # Combined article+law variants (highest specificity)
    if law_name:
        abbr = _normalize_law(law_name).upper()
        for prefix in ("статья", "ст.", "статьи", "статье", "статью"):
            variants.append(f"{prefix} {num} {abbr}")

    # Article-only variants
    for prefix in ("статья", "ст.", "статьи", "статье", "статью"):
        variants.append(f"{prefix} {num}")

    variants.append(num)
    return variants
