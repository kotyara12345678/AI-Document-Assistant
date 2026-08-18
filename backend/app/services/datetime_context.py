"""Current date/time context injected into LLM prompts.

The model cannot know "today" on its own: without this, the instruction
"use today's date as a presentational value" forces it to invent or guess a
date, which produces wrong dates in generated documents. This module builds a
short, deterministic, Russian-language note with the current date and time so
the agent can fill date fields correctly instead of hallucinating them.
"""

from datetime import datetime

_WEEKDAYS = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)

_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def _fmt_datetime(now: datetime) -> str:
    day = int(now.strftime("%d"))
    weekday = _WEEKDAYS[now.weekday()]
    month = _MONTHS[now.month - 1]
    time = now.strftime("%H:%M")
    return f"{day} {month} {now.year} года ({weekday})"


def current_datetime_note(now: datetime | None = None) -> str:
    """Short Russian sentence stating the current date and time.

    A fresh value is computed on every call (no caching), so a long-running
    agent turn still reflects the real moment the note was built.
    """
    now = now or datetime.now()
    date_text = _fmt_datetime(now)
    return (
        f"Текущие дата и время: {date_text}, {now.strftime('%H:%M')}. "
        "Используй именно эту дату, когда в документе нужно указать "
        "«сегодняшний день», дату подписания или дату составления."
    )