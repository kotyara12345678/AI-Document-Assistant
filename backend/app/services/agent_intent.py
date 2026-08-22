"""Deterministic intent gate for the document agent.

Routing policy — who may even see the document tools.

The policy is an ALLOW-list, not a word blacklist: a user request is routed to
the document tools ONLY when it carries a real signal pointing at the user's
files — an explicit document action (create / search / read / edit / compare /
list), a reference to the user's files/documents, or a fact question about
information that could live in those files (salaries, amounts, dates, people,
projects).

Messages without any such signal — greetings, thanks, agreement, small talk,
questions about the assistant itself, pure general knowledge — are answered in
plain text, and the document tools are deliberately NOT offered to the model
(the ``functions`` payload is omitted, and an LLM cannot call a tool it was
not given). This is what makes a chat message like «привет» unable to
accidentally trigger search/create/read/edit/compare.

Three-way classification:

* ``DOCUMENT``       — the request may and should use document tools.
* ``CONVERSATIONAL`` — never uses document tools; they are withheld.
* ``UNCERTAIN``      — no explicit signal either way; tools are allowed only
  when an active document task/context exists in this chat (pinned documents
  or a previous tool turn), otherwise they are withheld too, so a casual
  follow-up can never fire a document tool by accident.
"""

DOCUMENT = "document"
CONVERSATIONAL = "conversational"
UNCERTAIN = "uncertain"

# --- Explicit document action verbs -----------------------------------------
_ACTION_VERBS = (
    # create / generate / prepare / form
    "создай", "создать", "создание", "создам",
    "сгенерируй", "сгенерировать",
    "сформируй", "сформировать",
    "подготовь", "подготовить",
    "составь", "составить",
    "оформи", "оформить",
    "заполни", "заполнить",
    # search / find
    "найди", "найти", "ищи", "поищи", "искать",
    "поиск", "поиске", "поиском", "поиску",
    # read / extract from a document
    "прочитай", "прочитать", "прочти", "читай", "читат",
    # edit / translate / rewrite
    "переведи", "перевести", "перевод",
    "отредактируй", "отредактировать", "редактиру",
    "перепиши", "переписать", "перефраз",
    "улучши", "улучшить", "сократи", "сократить",
    "упрости", "упростить", "измени", "изменить",
    "переделай", "переработай", "очисти", "почисти",
    "исправь", "исправить",
    # compare
    "сравни", "сравнить", "сравнение",
    "разница", "отличия", "отличая", "чем отличаются",
    # list / enumerate / count files
    "перечисли", "перечисля", "перечисление", "насчитай",
    "список", "какие у меня", "какие файлы",
    "сколько у меня", "все мои документы", "все мои файлы",
)

# --- References to the user's files / documents -----------------------------
_DOCUMENT_NOUNS = (
    "документ", "документа", "документы", "документов", "документе",
    "документу", "документами", "документах",
    "файл", "файла", "файлы", "файлов", "файле", "файлах", "файлами",
    "договор", "договора", "договору", "договором", "договоре", "договоров",
    "контракт", "контракта", "контракте",
    "соглашение", "соглашения",
    "шаблон", "шаблона", "шаблону", "шаблоном", "шаблоне",
    "отчет", "отчёт", "отчета", "отчёта", "отчету", "отчёту",
    "справка", "справку", "справки",
    "резюме", "спецификация", "спецификацию", "доверенность",
    "акт выполненных работ",
    "pdf", "docx", "odt",
)

# --- Fact questions about data that could live in the user's files ----------
_FACT_WORDS = (
    # money / salaries / payments
    "зарплат", "оклад", "бюджет", "стоимост",
    "оплат", "заплат", "выплат", "доход", "выручк", "прибыл",
    "платеж", "задолженност", "штраф", "пени",
    # people in the organisation
    "сотрудник", "работник",
    "заказчик", "исполнитель", "поставщик", "подрядчик", "контрагент",
    "руководител", "директор",
    "получает", "получил", "кто такой", "кто такая",
    # projects / time / status / finances
    "проект", "статус", "срок", "дедлайн", "этап",
    "смет", "финанс",
    # contact details
    "телефон", "номер", "адрес", "реквизит", "email", "почт",
)

# --- Clearly non-document messages (checked only when NO document signal) ----
_CONVERSATIONAL_MARKERS = (
    # greetings
    "привет", "здравств", "добрый день", "доброе утро", "добрый вечер",
    "доброго дня", "приветствую", "здорово", "hello", "hi",
    # thanks
    "спасибо", "благодарю",
    # farewell
    "пока", "до свидани", "до встречи", "спокойной ночи", "до связи",
    # agreement / acknowledgement
    "понятно", "ясно", "ок", "окей", "ладно", "хорошо", "отлично", "супер",
    "принято", "ага",
    # small talk
    "как дела", "как ты", "как настроение", "что нового", "чем занимаешься",
    # about the assistant itself
    "что ты умеешь", "на что ты способен", "ты умеешь", "ты способен",
    "чего ты умеешь", "чем ты можешь помочь", "что ты можешь",
    "расскажи о себе", "расскажи про себя", "кто ты", "кто ты такой",
    "как тебя зовут", "как вас зовут",
    # generic help without a document target
    "помоги", "помогите", "помоги разобраться", "помоги понять",
    "разберись", "объясни",
)


def _q(question: str) -> str:
    return (question or "").strip().lower()


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(n in text for n in needles)


# --- Forced document search signals ------------------------------------------
# These patterns indicate the user explicitly wants to retrieve information
# from their documents.  When ANY of these is matched, the backend MUST run
# document search before the LLM can answer — the model is not allowed to
# skip search and answer from its own knowledge.

_FORCED_SEARCH_PREFIXES = (
    "найди", "найти", "ищи", "поищи",
    "процитируй", "цитируй", "цитату",
    "перескажи", "опиши",
    "что написано", "что сказано", "что говорится", "что указано",
    "какая статья", "какие статьи", "в каком разделе",
    "сколько стоит", "какая сумма", "какой размер",
    "кто подписан", "кто указ", "кто заказчик", "кто исполнител",
)

_FORCED_SEARCH_CONTAINS = (
    "статью", "статья", "статьи", "статье", "статьей",
    "главу", "глава", "главы", "главе",
    "раздел", "разделе", "разделу",
    "документ", "файл", "договор", "контракт",
    "моих документ", "мои файл", "мои документ",
    "загружен", "загрузил", "загрузк",
    "ук рф", "гк рф", "коап", "тк рф", "конституци",
    "уголовн", "гражданск", "трудов",
    "используй только мои", "только из моих",
    "в моих документах", "в моем файле", "в документе",
)

# --- Follow-up patterns: questions about PREVIOUS search results -----------
# These are conversational follow-ups that reference a previous turn's search
# results.  They must NOT trigger a new forced search — the model should
# answer from the conversation history.
_FOLLOWUP_PATTERNS = (
    "в каком документе", "в каком файле", "какой документ", "какой файл",
    "какие документы", "какие файлы", "какого документа", "какого файла",
    "где ты нашел", "где ты нашёл", "где вы нашли", "откуда это",
    "откуда ты взял", "откуда вы взяли", "источник", "источнике",
    "ты это нашел", "ты это нашёл", "вы это нашли",
    "ты взял", "вы взяли", "ты написал", "вы написали",
    "это где", "это откуда", "про какой документ",
    "про какой файл", "про какую статью", "про какой закон",
    "какой закон", "какой кодекс", "какой акт",
)

# --- Listing/enumeration patterns: must use list_documents tool -----------
# These queries ask for ALL documents, not a relevance-ranked subset.
# Forced search (AGENT_TOP_K=3) would return only 3 docs — the model
# MUST call list_documents instead.
_LISTING_PATTERNS = (
    "список документов", "список файлов", "все документы", "все файлы",
    "все мои документы", "все мои файлы", "список всех",
    "какие у меня документы", "какие у меня файлы",
    "какие документы у меня", "какие файлы у меня",
    "назови все документы", "назови все файлы",
    "назови документы", "назови файлы",
    "покажи список документов", "покажи список файлов",
    "покажи документы", "покажи файлы",
    "перечисли документы", "перечисли файлы",
    "сколько у меня документов", "сколько у меня файлов",
    "сколько документов", "сколько файлов",
    "выдай список", "выдай все документы", "выдай все файлы",
    "имена моих файлов", "имена документов",
)


# --- Creation patterns: must use create_document tool -----------
# These queries ask the user to GENERATE content, not RETRIEVE existing data.
# Forced search is wasteful and confusing for creation requests.
_CREATION_PATTERNS = (
    "создай", "создай", "сгенерируй", "сформируй", "сделай",
    "подготовь", "напиши", "оформи", "составь", "заполни",
    "создать", "сгенерировать", "сформировать", "сделать",
    "подготовить", "написать", "оформить", "составить", "заполнить",
    "создании", "создание", "генерац", "формир",
)


def is_forced_document_query(question: str) -> bool:
    """True when the query MUST go through document search before answering.

    This catches queries where the user explicitly asks to retrieve, quote,
    or describe content from their documents.  The backend will run
    search_documents proactively and inject results into context — the LLM
    cannot skip search and answer from its own knowledge.

    Follow-up questions about previous search results (e.g. "в каком
    документе ты это нашел?") are excluded — the model should answer from
    conversation history, not trigger a new search.
    """
    q = _q(question)
    if not q:
        return False
    # Follow-up questions about previous results should NOT trigger forced search
    if _has_any(q, _FOLLOWUP_PATTERNS):
        return False
    # Listing/enumeration queries must use list_documents tool, NOT forced search
    # (forced search only returns top_k=3, not all documents)
    if _has_any(q, _LISTING_PATTERNS):
        return False
    # Creation requests should NOT trigger forced search — the model needs
    # to call create_document, not search_documents.  Creation verbs indicate
    # the user wants to GENERATE content, not RETRIEVE existing content.
    if _has_any(q, _CREATION_PATTERNS):
        return False
    if _has_any(q, _FORCED_SEARCH_PREFIXES):
        return True
    if _has_any(q, _FORCED_SEARCH_CONTAINS):
        return True
    return False


def resolve_intent(question: str) -> str:
    """Classify a request into DOCUMENT, CONVERSATIONAL or UNCERTAIN.

    A document signal always wins (an explicitly document-related message may
    still be answered in plain text by the model — re-enabling tools never
    forces a call). CONVERSATIONAL applies only when there is NO document
    signal at all. Everything else is UNCERTAIN.
    """
    q = _q(question)
    if not q:
        return CONVERSATIONAL
    if _has_any(q, _ACTION_VERBS + _DOCUMENT_NOUNS + _FACT_WORDS):
        return DOCUMENT
    if _has_any(q, _CONVERSATIONAL_MARKERS):
        return CONVERSATIONAL
    return UNCERTAIN


def tools_enabled(question: str, *, has_document_context: bool = False) -> bool:
    """Whether the document tools may be offered to the model this turn.

    * DOCUMENT       -> True  (the request is about the user's files)
    * CONVERSATIONAL -> False (a greeting/thanks/small-talk must never call tools)
    * UNCERTAIN      -> only when an active document context exists, so a
      follow-up of a running document task still has tools while a fresh
      floating message does not.
    """
    intent = resolve_intent(question)
    if intent == DOCUMENT:
        return True
    if intent == CONVERSATIONAL:
        return False
    return has_document_context


# --- Is the request explicitly about CREATING/generating a file? ------------
_CREATION_VERBS = (
    "создай", "создать", "создание", "сгенерируй", "сгенерировать",
    "сформируй", "сформировать", "подготовь", "подготовить",
    "составь", "составить", "оформи", "оформить", "заполни", "заполнить",
    "сделай документ", "сделай файл", "сделай договор",
    "сделай отчёт", "сделай отчет", "сделай pdf", "сделай справку",
    "сделай резюме", "сделай любой", "сделай пример", "сделай шаблон",
    "напиши документ", "создай в формате", "создай как файл",
)


def is_creation_request(question: str) -> bool:
    """True when the request is explicitly about creating/generating a file.

    Used by the anti-fabrication guard to decide whether a model answer that
    claims success without a real tool result should be replaced with the
    "file was not created / data is missing" phrasing. For non-creation
    requests that phrasing is meaningless and must not be shown.
    """
    q = _q(question)
    if _has_any(q, _CREATION_VERBS):
        return True
    # "создай X" / "сгенерируй X" / "сделай X" + a document noun also counts,
    # even when the verb form is not listed verbatim above.
    return (
        _has_any(q, ("создай", "создать", "сгенерируй", "сгенерировать", "сделай"))
        and _has_any(q, _DOCUMENT_NOUNS)
    )