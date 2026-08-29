from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.credit_application import ApplicationChannel, ApplicationStatus


class ApplicationCreate(BaseModel):
    customer_id: int
    product_id: int
    requested_amount: float = Field(gt=0)
    requested_tenor_months: int = Field(gt=0, le=120)
    channel: ApplicationChannel
    created_by: str = "system"


class AssessmentResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    decision: str
    estimated_installment: float
    debt_burden_ratio: float | None
    triggered_rules: list
    config_snapshot: dict
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
