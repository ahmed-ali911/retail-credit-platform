from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.core.references import format_reference
from app.models.credit_application import (
    ApplicationChannel,
    ApplicationStatus,
    AssessmentSource,
)


class ApplicationCreate(BaseModel):
    customer_id: int
    product_id: int
    requested_amount: float = Field(gt=0)
    requested_tenor_months: int = Field(gt=0, le=120)
    channel: ApplicationChannel
    created_by: str = "system"


class ReviewDecision(str, enum.Enum):
    approved = "approved"
    rejected = "rejected"
    return_for_info = "return_for_info"


class ReviewRequest(BaseModel):
    decision: ReviewDecision
    reason: str = Field(min_length=1, max_length=1000)


class AssessmentResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    decision: str
    source: AssessmentSource
    estimated_installment: float
    debt_burden_ratio: float | None
    triggered_rules: list
    config_snapshot: dict
    reviewed_by: int | None = None
    notes: str | None = None
    created_at: datetime


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int
    product_id: int
    requested_amount: float
    requested_tenor_months: int
    channel: ApplicationChannel
    status: ApplicationStatus
    created_at: datetime
    created_by: str
    latest_assessment: AssessmentResultOut | None = None
    assessments: list[AssessmentResultOut] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reference_code(self) -> str:
        return format_reference("CreditApplication", self.id)


class ApplicationListItem(BaseModel):
    """Compact row for the Step 9 review queue (`GET /applications?status=…`).

    There is no separate `submitted_at` column yet — `submitted_at` is the
    application's `created_at` (the submit flow requires `draft` status and runs
    assessment synchronously, so the two moments are effectively the same).
    """

    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int
    product_id: int
    requested_amount: float
    status: ApplicationStatus
    submitted_at: datetime = Field(validation_alias="created_at")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reference_code(self) -> str:
        return format_reference("CreditApplication", self.id)
