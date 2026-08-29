from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.collections import (
    CollectionActivityType,
    CollectionCaseStatus,
    PromiseStatus,
)


class ActivityCreate(BaseModel):
    activity_type: CollectionActivityType
    notes: str | None = None
    # Only used when activity_type == promise_to_pay.
    promised_amount: float | None = None
    promised_date: date | None = None


class CollectionActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    collection_case_id: int
    created_by: int
    activity_type: CollectionActivityType
    notes: str | None
    created_at: datetime
    promised_amount: float | None
    promised_date: date | None
    promise_status: PromiseStatus | None


class CollectionCaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    contract_id: int
    status: CollectionCaseStatus
    opened_at: datetime
    opened_reason: str
    closed_at: datetime | None


class CollectionCaseDetailOut(CollectionCaseOut):
    activities: list[CollectionActivityOut] = []
