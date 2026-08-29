from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int | None
    action: str
    entity_type: str
    entity_id: str | None
    before_value: dict | None
    after_value: dict | None
    timestamp: datetime
