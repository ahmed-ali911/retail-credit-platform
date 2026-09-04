from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.reconciliation import ReconExceptionReason, ReconExceptionStatus


class BankLineCreate(BaseModel):
    bank_reference: str = Field(min_length=1, max_length=100)
    amount: float = Field(gt=0)
    value_date: date


class BankStatementLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    bank_reference: str
    amount: float
    value_date: date
    imported_at: datetime
    matched_payment_id: int | None


class MatchRunResult(BaseModel):
    lines_processed: int
    matched: int
    exceptions_created: int


class RejectedUploadRow(BaseModel):
    row: int
    reason: str


class BankLineUploadResult(BaseModel):
    """Step 15, Part E — bulk .xlsx ingestion summary."""

    rows_processed: int
    rows_ingested: int
    rows_rejected: int
    rejected: list[RejectedUploadRow]
    matched: int
    exceptions_created: int


class ReconciliationExceptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    bank_line_id: int
    reason: ReconExceptionReason
    status: ReconExceptionStatus
    created_at: datetime
    resolved_at: datetime | None
    resolved_by: int | None


class ManualMatchRequest(BaseModel):
    payment_id: int
    reason: str = Field(min_length=1, max_length=500)


class ReconciliationStatusOut(BaseModel):
    unreconciled_payments: int
    reconciled_payments: int
    exception_payments: int
    open_exceptions: int
    resolved_exceptions: int
    unmatched_bank_lines: int
