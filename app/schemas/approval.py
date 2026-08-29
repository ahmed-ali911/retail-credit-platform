from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.approval import ApprovalStatus


class WaiverRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class DecisionRequest(BaseModel):
    reason: str | None = None


class ApprovalRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    action_type: str
    entity_type: str
    entity_id: str
    requested_by: int
    requested_at: datetime
    payload: dict
    status: ApprovalStatus
    decided_by: int | None
    decided_at: datetime | None
    decision_notes: str | None
