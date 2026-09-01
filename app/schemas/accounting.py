from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.accounting import AccountingEventType, AccountingStatus


class AccountingEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_type: AccountingEventType
    event_reference: str
    contract_id: int
    customer_id: int | None
    amount: float
    currency: str
    event_date: datetime
    accounting_status: AccountingStatus
    external_gl_reference: str | None
    error_message: str | None
    retry_count: int
    created_at: datetime


class PostAccountingEventsResult(BaseModel):
    events_considered: int
    posted: int
    failed: int
