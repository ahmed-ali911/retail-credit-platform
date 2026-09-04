from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.core.references import format_reference
from app.models.payment import PaymentReconciliationStatus, PaymentStatus


class PaymentCreate(BaseModel):
    amount: float = Field(gt=0)
    external_reference: str = Field(min_length=1, max_length=100)


class PaymentAllocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    installment_id: int
    late_fee_amount: float
    profit_amount: float
    principal_amount: float
    total: float


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    contract_id: int
    amount: float
    external_reference: str
    gateway_reference: str | None = None
    received_at: datetime
    status: PaymentStatus
    reconciliation_status: PaymentReconciliationStatus = (
        PaymentReconciliationStatus.unreconciled
    )
    allocated_amount: float
    unallocated_amount: float
    allocations: list[PaymentAllocationOut]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reference_code(self) -> str:
        return format_reference("Payment", self.id)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def contract_reference(self) -> str:
        return format_reference("InstallmentContract", self.contract_id)


class PaymentResult(BaseModel):
    replayed: bool
    payment: PaymentOut


class ReceivableOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    contract_id: int
    outstanding_principal: float
    outstanding_profit: float
    outstanding_receivable: float
    outstanding_late_fees: float
    total_installments_paid: int
    total_installments_remaining: int
    reconciliation_summary: dict[str, int] = {}  # P0-5, additive

    @computed_field  # type: ignore[prop-decorator]
    @property
    def contract_reference(self) -> str:
        return format_reference("InstallmentContract", self.contract_id)


class AssessOverdueRequest(BaseModel):
    # Optional simulated "run date" for manual/test triggering.
    as_of: date | None = None


class OverdueChargeOut(BaseModel):
    installment_id: int
    sequence_number: int
    dpd: int
    amount: float


class AssessOverdueResult(BaseModel):
    as_of: date
    grace_period_days: int
    installments_marked_overdue: int
    late_fees_assessed: int
    total_late_fee_amount: float
    collection_cases_opened: int = 0
    charges: list[OverdueChargeOut]
