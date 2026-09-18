from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.write_off import WriteOffEligibilityStatus, WriteOffRequestStatus, WriteOffType


# --------------------------------------------------------------------------- #
# Eligibility
# --------------------------------------------------------------------------- #
class EligibilityIndicatorOut(BaseModel):
    id: str
    name: str
    category: str
    result: str
    detail: str


class EligibilityResultOut(BaseModel):
    contract_id: int
    status: WriteOffEligibilityStatus
    indicators: list[EligibilityIndicatorOut]
    dpd: int | None
    ecl_stage: int | None
    ecl_amount: float | None
    provision_amount: float | None
    collections_case_id: int | None
    collections_case_status: str | None


# --------------------------------------------------------------------------- #
# Write-off request (maker side)
# --------------------------------------------------------------------------- #
class WriteOffRequestCreate(BaseModel):
    write_off_type: WriteOffType
    reason_code: str
    justification: str = Field(min_length=10)
    evidence_ref: str | None = None
    comments: str | None = None
    # Mandatory server-side (services/write_off.py) whenever the system's own
    # eligibility verdict is NOT_ELIGIBLE or UNAVAILABLE — a controlled
    # exception, still subject to the same maker-checker.
    exception_justification: str | None = None
    # PARTIAL only; ignored (auto-computed from the outstanding snapshot) for FULL.
    requested_principal: float | None = Field(default=None, ge=0)
    requested_profit: float | None = Field(default=None, ge=0)
    requested_late_fee: float | None = Field(default=None, ge=0)
    requested_other_charges: float | None = Field(default=None, ge=0)


class WriteOffRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    contract_id: int
    customer_id: int | None
    write_off_type: WriteOffType
    status: WriteOffRequestStatus
    reason_code: str
    justification: str
    evidence_ref: str | None
    comments: str | None

    eligibility_status: WriteOffEligibilityStatus
    eligibility_snapshot: list[dict]
    is_exception: bool
    exception_justification: str | None

    snapshot_principal_outstanding: float
    snapshot_profit_outstanding: float
    snapshot_late_fee_outstanding: float
    snapshot_other_charges_outstanding: float
    snapshot_total_outstanding: float
    snapshot_dpd: int | None
    snapshot_ecl_stage: int | None
    snapshot_ecl_amount: float | None
    snapshot_provision_amount: float | None
    snapshot_collections_case_status: str | None
    snapshot_collections_case_id: int | None

    requested_principal: float
    requested_profit: float
    requested_late_fee: float
    requested_other_charges: float

    approval_request_id: int | None
    requested_by: int | None
    approved_by: int | None
    created_at: datetime
    decided_at: datetime | None


class WriteOffRequestCancelIn(BaseModel):
    reason: str | None = None


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
class WriteOffExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    write_off_request_id: int
    contract_id: int
    customer_id: int | None
    write_off_type: WriteOffType

    executed_principal: float
    executed_profit: float
    executed_late_fee: float
    executed_other_charges: float

    remaining_principal: float
    remaining_profit: float
    remaining_late_fee: float

    ecl_stage_snapshot: int | None
    ecl_amount_snapshot: float | None
    provision_amount_snapshot: float | None

    contract_closure_id: int | None
    collection_case_id: int | None
    accounting_event_id: int | None

    executed_by: int | None
    executed_at: datetime


class WriteOffExecutionResult(BaseModel):
    replayed: bool
    execution: WriteOffExecutionOut


# --------------------------------------------------------------------------- #
# Recovery
# --------------------------------------------------------------------------- #
class RecoveryCreate(BaseModel):
    amount: float = Field(gt=0)
    # Mandatory — a recovery must always be traceable to a real payment/cash
    # source, never an unexplained arbitrary amount.
    external_reference: str = Field(min_length=1, max_length=120)
    payment_id: int | None = None
    currency: str = Field(default="KWD", min_length=3, max_length=3)
    recovery_date: date | None = None  # defaults to today
    channel: str | None = Field(default=None, max_length=50)


class RecoveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    write_off_execution_id: int
    payment_id: int | None
    external_reference: str
    amount: float
    currency: str
    recovery_date: date
    channel: str | None
    allocated_principal: float
    allocated_profit: float
    allocated_late_fee: float
    allocated_other_charges: float
    accounting_event_id: int | None
    recorded_by: int | None
    created_at: datetime


class WriteOffExecutionDetailOut(WriteOffExecutionOut):
    recoveries: list[RecoveryOut] = []
    total_recovered: float = 0.0
    remaining_recoverable: float = 0.0
