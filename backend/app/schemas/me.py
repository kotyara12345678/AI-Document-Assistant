from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.auth import UserOut


class MeStats(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user: UserOut
    documents_total: int
    chats_total: int
    messages_total: int
    tokens_used: int
    last_active_at: datetime | None
