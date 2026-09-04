from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, computed_field

from app.core.references import format_reference
from app.models.contract import ContractStatus, InstallmentStatus
from app.models.payment import LateFeeStatus
from app.schemas.closure import ContractClosureOut


class SalesOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    application_id: int
    product_id: int
    offer_id: int
    sale_price: float
    down_payment_amount: float
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reference_code(self) -> str:
        return format_reference("SalesOrder", self.id)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def application_reference(self) -> str:
        return format_reference("CreditApplication", self.application_id)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def product_reference(self) -> str:
        return format_reference("Product", self.product_id)


class LateFeeChargeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    installment_id: int
    contract_id: int
    amount: float
    amount_paid: float
    outstanding: float
    status: LateFeeStatus
    assessed_at: datetime


class InstallmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    contract_id: int
    sequence_number: int
    due_date: date
    principal_component: float
    profit_component: float
    principal_paid: float
    profit_paid: float
    principal_outstanding: float
    profit_outstanding: float
    late_fee_outstanding: float
    total_due: float
    status: InstallmentStatus
    late_fee_charges: list[LateFeeChargeOut] = []


class ContractOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sales_order_id: int
    tenor_months: int
    total_profit: float
    unearned_profit_balance: float
    status: ContractStatus
    created_at: datetime
    activated_at: datetime | None
    sales_order: SalesOrderOut
    installments: list[InstallmentOut]
    late_fee_charges: list[LateFeeChargeOut] = []
    closure: ContractClosureOut | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reference_code(self) -> str:
        return format_reference("InstallmentContract", self.id)


class AcceptResult(BaseModel):
    offer_id: int
    sales_order_id: int
    contract_id: int
    contract: ContractOut

    @computed_field  # type: ignore[prop-decorator]
    @property
    def contract_reference(self) -> str:
        return format_reference("InstallmentContract", self.contract_id)
