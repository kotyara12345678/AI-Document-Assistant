"""Per-user LLM token accounting behind a tiny persistence helper.

UsageLog rows are appended after each completed LLM call (chat, agent tool
loops, document editing) so the profile screen and future billing can show how
many tokens a user actually spent. The helper never fails a request: any DB
error is logged and swallowed so token accounting can never take down a chat
or agent turn.
"""

import logging

from sqlalchemy.orm import Session

from app.models.usage_log import UsageLog

logger = logging.getLogger("app.usage_log")


def record_tokens(db: Session, user_id: int, tokens: int) -> None:
    """Persist ``tokens`` consumed by one user. Never raises."""
    if not tokens or tokens < 0:
        return
    try:
        db.add(UsageLog(user_id=user_id, tokens_used=int(tokens)))
        db.commit()
    except Exception:
        logger.exception("Failed to record token usage for user %s", user_id)
        try:
            db.rollback()
        except Exception:
            pass